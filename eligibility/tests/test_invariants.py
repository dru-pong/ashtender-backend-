"""
Architectural invariants.

These do not test behaviour so much as guard the shape of the system.  Each one
encodes a decision that is cheap to hold now and expensive to recover later.
"""
import ast
import inspect
from datetime import date, timedelta
from pathlib import Path

from django.test import SimpleTestCase, TestCase

from core.enums import BLOCKING_VERDICTS, Verdict
from core.tests.factories import (
    attach_universal,
    make_document,
    make_org,
    make_spec,
    make_tender,
)
from eligibility.engine import evaluate
from eligibility.models import EligibilityCheck, RequirementFinding

ELIGIBILITY_DIR = Path(__file__).resolve().parent.parent


class VerdictIsNotABooleanTests(SimpleTestCase):
    def test_four_distinct_verdicts_exist(self):
        self.assertEqual(
            set(Verdict.values), {"PASS", "FAIL", "UNKNOWN", "WAIVABLE"}
        )

    def test_unknown_is_blocking(self):
        """
        UNKNOWN routes to a human. Treating it as a soft pass is how a system
        quietly submits a bid against an unconfirmed document.
        """
        self.assertIn(Verdict.UNKNOWN, BLOCKING_VERDICTS)
        self.assertIn(Verdict.FAIL, BLOCKING_VERDICTS)
        self.assertNotIn(Verdict.PASS, BLOCKING_VERDICTS)

    def test_verdict_fields_are_char_not_boolean(self):
        from django.db import models

        for model in (EligibilityCheck, RequirementFinding):
            with self.subTest(model=model.__name__):
                field = model._meta.get_field("verdict")
                self.assertIsInstance(field, models.CharField)
                self.assertNotIsInstance(field, models.BooleanField)


class EngineDoesNotConsultAModelTests(SimpleTestCase):
    """
    The LLM extracts; Python decides.  A model may report that a notice demands
    a Labour Certificate, but only the engine decides whether that requirement
    is met -- so an eligibility verdict is reproducible, explainable, and
    unaffected by a provider outage.
    """

    def _imported_modules(self, filename):
        tree = ast.parse((ELIGIBILITY_DIR / filename).read_text())
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    def test_engine_does_not_import_the_llm_app(self):
        for module in self._imported_modules("engine.py"):
            self.assertFalse(
                module == "llm" or module.startswith("llm."),
                f"eligibility/engine.py imports {module}",
            )

    def test_rules_do_not_import_the_llm_app(self):
        for module in self._imported_modules("rules.py"):
            self.assertFalse(module == "llm" or module.startswith("llm."))

    def test_rules_do_not_touch_the_database(self):
        """
        rules.py is pure so the arithmetic that decides whether a firm can bid
        is testable without fixtures.
        """
        source = (ELIGIBILITY_DIR / "rules.py").read_text()
        self.assertNotIn(".objects", source)
        for module in self._imported_modules("rules.py"):
            self.assertFalse(module.endswith(".models"))

    def test_the_gateway_forbids_verdicts_in_every_seeded_prompt(self):
        from llm.management.commands.seed_llm_routes import EXTRACT_ONLY, ROUTES

        self.assertIn("never decide", EXTRACT_ONLY)
        extraction_routes = [route for route in ROUTES if route[4]]
        self.assertTrue(extraction_routes)
        for route in extraction_routes:
            with self.subTest(task=route[0]):
                self.assertIn("You extract information", route[6])

    def test_no_route_schema_contains_a_verdict_field(self):
        from llm.management.commands.seed_llm_routes import ROUTES

        for task, _model, _temp, _tokens, schema, _vision, _prompt in ROUTES:
            with self.subTest(task=task):
                keys = str(schema).lower()
                for forbidden in ("verdict", "eligible", "qualified", "compliant"):
                    self.assertNotIn(forbidden, keys)


class FindingsCarryTheirEvidenceTests(TestCase):
    """
    Spec invariant: a finding that cannot quote the sentence it came from, or
    name the document that answers it, does not ship.  A system that tells a
    contractor they are ineligible without showing its work will not be
    believed, and should not be.
    """

    def test_every_finding_has_a_human_readable_reason(self):
        org = make_org()
        spec = make_spec()
        attach_universal(spec)
        check = evaluate(org, make_tender())
        self.assertTrue(check.findings.exists())
        for finding in check.findings.for_org(org.pk):
            with self.subTest(code=finding.code):
                self.assertTrue(finding.reason.strip())
                self.assertNotEqual(finding.reason.strip(), finding.code)

    def test_a_blocking_document_finding_offers_a_remedy(self):
        org = make_org()
        spec = make_spec()
        attach_universal(spec)
        finding = evaluate(org, make_tender()).findings.get(spec=spec)
        self.assertIn(finding.verdict, BLOCKING_VERDICTS)
        self.assertTrue(finding.remedy.strip())

    def test_a_passing_document_finding_names_the_file(self):
        org = make_org()
        spec = make_spec()
        attach_universal(spec)
        doc = make_document(
            org, spec, expires_on=date.today() + timedelta(days=200), verified=True
        )
        finding = evaluate(org, make_tender()).findings.get(spec=spec)
        self.assertEqual(finding.satisfied_by_id, doc.pk)

    def test_an_explicit_requirement_keeps_the_tender_sentence(self):
        from core.tests.factories import make_category, require_class

        org = make_org()
        tender = make_tender()
        sentence = "Tenderers shall hold MRH classification A2 or better."
        require_class(tender, make_category(letter="A"), min_class=2, source_text=sentence)
        finding = evaluate(org, tender).findings.get(
            code=RequirementFinding.Code.CLASSIFICATION
        )
        self.assertEqual(finding.source_text, sentence)


class UnverifiedExtractionNeverReachesSubmissionTests(TestCase):
    """
    Spec invariant: an unverified extraction may support a screening PASS but
    may never support READY_TO_SUBMIT.
    """

    def test_extracted_but_unconfirmed_document_yields_a_blocking_verdict(self):
        org = make_org()
        spec = make_spec()
        attach_universal(spec)
        make_document(
            org, spec, expires_on=date.today() + timedelta(days=365),
            extracted=True, verified=False,
        )
        check = evaluate(org, make_tender())
        finding = check.findings.get(spec=spec)
        self.assertEqual(finding.verdict, Verdict.UNKNOWN)
        self.assertIn(check.verdict, BLOCKING_VERDICTS)

    def test_a_bid_cannot_be_ready_while_the_check_is_blocking(self):
        from bids.models import Bid
        from core.enums import BidState

        org = make_org()
        spec = make_spec()
        attach_universal(spec)
        make_document(
            org, spec, expires_on=date.today() + timedelta(days=365),
            extracted=True, verified=False,
        )
        tender = make_tender()
        check = evaluate(org, tender)
        bid = Bid.objects.create(organization=org, tender=tender, latest_check=check)
        self.assertIn(bid.latest_check.verdict, BLOCKING_VERDICTS)
        self.assertNotEqual(bid.state, BidState.READY_TO_SUBMIT)


class ChecksAreImmutableTests(TestCase):
    def test_the_engine_never_updates_an_existing_check(self):
        """
        Re-running produces a new row. "Why did it say that last Tuesday?" can
        only be answered if last Tuesday's verdict still exists.
        """
        source = inspect.getsource(evaluate)
        self.assertNotIn("update_or_create", source)
        self.assertNotIn("check.save(update_fields", source)

    def test_engine_version_is_recorded_on_every_check(self):
        from eligibility.rules import ENGINE_VERSION

        check = evaluate(make_org(), make_tender())
        self.assertEqual(check.engine_version, ENGINE_VERSION)


class PayerScoreDoesNotLeakTests(TestCase):
    """
    PayerScore is the one deliberate cross-tenant read. It aggregates IPC
    dates and nothing else -- never Interaction notes, which are a tenant's
    relationship capital.
    """

    def test_payer_score_has_no_tenant_column(self):
        from core.tenancy import TenantOwned
        from receivables.models import PayerScore

        self.assertFalse(issubclass(PayerScore, TenantOwned))
        self.assertNotIn(
            "organization", [field.name for field in PayerScore._meta.get_fields()]
        )

    def test_the_aggregator_reads_only_ipcs(self):
        source = Path(__file__).resolve().parents[2] / "receivables" / "payer_score.py"
        text = source.read_text()
        self.assertNotIn("Interaction", text)
        self.assertNotIn("bids", text)

    def test_only_the_two_sanctioned_modules_cross_tenants(self):
        """
        Parsed, not grepped: an error message that merely names
        ``across_tenants()`` is not a caller, and a substring search cannot
        tell the difference.

        Exactly two callers are sanctioned by the spec -- the PayerScore
        aggregation and Django admin -- and this allowlist is the audit
        record. A third should arrive as a failing test and a deliberate
        decision, not as a quiet import.
        """
        sanctioned = {"receivables/payer_score.py", "core/admin.py"}
        root = Path(__file__).resolve().parents[2]
        callers = set()
        for path in root.glob("*/*.py"):
            if "tests" in path.parts or path.name == "tenancy.py":
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "across_tenants"
                ):
                    callers.add(f"{path.parent.name}/{path.name}")
        self.assertEqual(callers, sanctioned)

    def test_the_admin_cross_tenant_read_names_the_staff_user(self):
        """A support action is legitimate; an anonymous one is not auditable."""
        source = Path(__file__).resolve().parents[2] / "core" / "admin.py"
        text = source.read_text()
        self.assertIn("request.user", text)
        self.assertIn("django-admin", text)
