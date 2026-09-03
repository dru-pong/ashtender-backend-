"""
Phase 2 acceptance: the seed is idempotent and the classification catalogue
matches the published schemes.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from catalog.models import (
    ClassificationScheme,
    DomainRequirementRule,
    FinancialClassCap,
    ProcurementDomain,
    Region,
    RequirementSpec,
    WorkCategory,
)
from core.enums import RequirementTier


class SeedCatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_catalog", "--quiet-summary")

    def test_sixteen_regions(self):
        self.assertEqual(Region.objects.count(), 16)

    def test_both_ministry_schemes_exist_and_neither_register_is_ingestible(self):
        """
        Both registers are published as PDF, so no code path may assume it can
        fetch a machine-readable list.
        """
        codes = set(ClassificationScheme.objects.values_list("code", flat=True))
        self.assertTrue({"MWHWR", "MRH"}.issubset(codes))
        self.assertFalse(
            ClassificationScheme.objects.filter(register_ingestible=True).exists()
        )

    def test_class_ranges_are_per_letter_not_uniformly_four(self):
        ranges = {
            f"{wc.scheme.code}:{wc.letter}": wc.max_financial_class
            for wc in WorkCategory.objects.select_related("scheme")
        }
        self.assertEqual(ranges["MWHWR:D"], 4)
        self.assertEqual(ranges["MWHWR:K"], 4)
        self.assertEqual(ranges["MWHWR:E"], 3)
        self.assertEqual(ranges["MWHWR:G"], 2)
        self.assertEqual(ranges["MRH:A"], 4)
        self.assertEqual(ranges["MRH:B"], 4)

    def test_class_exists_respects_the_letter_maximum(self):
        electrical = WorkCategory.objects.get(letter="E")
        self.assertTrue(electrical.class_exists(3))
        self.assertFalse(electrical.class_exists(4))

    def test_every_value_cap_is_seeded_stale(self):
        """
        The circulating figures have no current primary source, so none of them
        may be trusted enough to fail a tender on.
        """
        self.assertTrue(FinancialClassCap.objects.exists())
        self.assertFalse(FinancialClassCap.objects.filter(is_stale=False).exists())

    def test_class_one_cap_is_unlimited(self):
        caps = FinancialClassCap.objects.filter(financial_class=1)
        self.assertTrue(caps.exists())
        self.assertTrue(all(cap.amount is None for cap in caps))

    def test_statutory_pack_is_attached_as_universal(self):
        universal = RequirementSpec.objects.filter(
            domainrequirementrule__tier=RequirementTier.UNIVERSAL
        ).distinct()
        codes = set(universal.values_list("code", flat=True))
        self.assertIn("tax-clearance", codes)
        self.assertIn("ssnit-clearance", codes)
        self.assertIn("ppa-registration", codes)
        self.assertIn("business-operating-permit", codes)

    def test_vat_is_conditional(self):
        """The STD qualifies VAT registration with 'if applicable'."""
        self.assertTrue(RequirementSpec.objects.get(code="vat-registration").is_conditional)

    def test_recency_months_is_set_wherever_the_mode_needs_it(self):
        from core.enums import ValidityMode

        for spec in RequirementSpec.objects.filter(validity_mode=ValidityMode.ISSUE_RECENCY):
            with self.subTest(spec=spec.code):
                self.assertIsNotNone(spec.recency_months)

    def test_no_region_except_ahafo_claims_a_complete_district_list(self):
        """
        There are 261 MMDAs and the seed ships a documented subset. A partial
        catalogue must not present itself as the full one.
        """
        complete = set(
            Region.objects.filter(district_seed_complete=True).values_list("code", flat=True)
        )
        self.assertEqual(complete, {"AH"})

    def test_bechem_and_sunyani_are_seeded(self):
        """Tenant zero's home district and its known contract's district."""
        from catalog.models import District

        self.assertTrue(District.objects.filter(name="Sunyani Municipal").exists())
        self.assertEqual(District.objects.get(name="Tano South Municipal").capital, "Bechem")


class IdempotencyTests(TestCase):
    def test_running_the_seed_twice_changes_nothing(self):
        call_command("seed_catalog", "--quiet-summary")
        before = {
            "regions": Region.objects.count(),
            "categories": WorkCategory.objects.count(),
            "specs": RequirementSpec.objects.count(),
            "domains": ProcurementDomain.objects.count(),
            "rules": DomainRequirementRule.objects.count(),
            "caps": FinancialClassCap.objects.count(),
        }
        call_command("seed_catalog", "--quiet-summary")
        after = {
            "regions": Region.objects.count(),
            "categories": WorkCategory.objects.count(),
            "specs": RequirementSpec.objects.count(),
            "domains": ProcurementDomain.objects.count(),
            "rules": DomainRequirementRule.objects.count(),
            "caps": FinancialClassCap.objects.count(),
        }
        self.assertEqual(before, after)


class SeedTenantZeroTests(TestCase):
    """
    The design-partner fixture, which is also the engine's calibration data.
    Its gaps are asserted as gaps: a blank TIN is the true state of knowledge.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_catalog", "--quiet-summary")
        call_command("seed_tenant_zero", stdout=StringIO())

    def setUp(self):
        from tenancy.models import Organization

        self.org = Organization.objects.get(slug="shamma-rohi")

    def test_organization_is_flagged_as_the_design_partner(self):
        self.assertTrue(self.org.is_design_partner)

    def test_legal_name_carries_the_limited_suffix_as_published(self):
        self.assertEqual(self.org.company_profile.legal_name, "Shamma Rohi Limited")

    def test_unknown_identifiers_are_left_blank(self):
        profile = self.org.company_profile
        self.assertEqual(profile.tin, "")
        self.assertEqual(profile.orc_registration_number, "")
        self.assertIsNone(profile.incorporation_date)
        self.assertIsNone(profile.ghanaian_shareholding_pct)

    def test_domestic_contractor_test_is_unknown_not_false(self):
        """Unrecorded shareholding must not be reported as a failed test."""
        self.assertIsNone(self.org.company_profile.is_domestic_contractor_by_ownership)

    def test_holds_a1_and_b1_both_lapsed(self):
        from datetime import date

        from vault.models import Classification

        held = Classification.objects.for_org(self.org.pk).select_related("category")
        self.assertEqual(
            {f"{c.category.letter}{c.financial_class}" for c in held}, {"A1", "B1"}
        )
        for classification in held:
            with self.subTest(classification=str(classification)):
                self.assertEqual(classification.valid_to, date(2022, 4, 2))
                self.assertFalse(classification.is_current_on(date(2026, 1, 1)))

    def test_registry_entry_records_the_published_sequence_number(self):
        from tenders.models import ClassificationRegistryEntry

        entry = ClassificationRegistryEntry.objects.get(company_name="Shamma Rohi Limited")
        self.assertEqual(entry.list_seq_no, 415)
        self.assertEqual(entry.notation, "A1B1")

    def test_the_2023_list_is_recorded_even_though_the_firm_is_absent_from_it(self):
        """
        "We have not read that register" and "that register omits this firm"
        are different states and must not look identical in the database.
        """
        from tenders.models import ClassificationRegistryEntry

        self.assertTrue(
            ClassificationRegistryEntry.objects.filter(
                company_name__startswith="__LIST_MARKER__"
            ).exists()
        )

    def test_bechem_claim_is_outstanding_in_full(self):
        from decimal import Decimal

        from receivables.models import Contract

        contract = Contract.objects.for_org(self.org.pk).get()
        self.assertEqual(contract.value, Decimal("12473675.00"))
        self.assertEqual(contract.outstanding, Decimal("12473675.00"))

    def test_placeholder_ipc_has_no_invented_dates(self):
        """PayerScore is built from real dates; a guessed one would poison it."""
        from receivables.models import IPC

        ipc = IPC.objects.for_org(self.org.pk).get()
        self.assertIsNone(ipc.submitted_on)
        self.assertIsNone(ipc.certified_on)
        self.assertIsNone(ipc.paid_on)

    def test_the_two_blocking_questions_are_queued_as_action_requests(self):
        from bids.models import ActionRequest

        kinds = set(
            ActionRequest.objects.for_org(self.org.pk)
            .filter(status=ActionRequest.Status.OPEN)
            .values_list("kind", flat=True)
        )
        self.assertIn(ActionRequest.Kind.VERIFY_CLASSIFICATION, kinds)
        self.assertIn(ActionRequest.Kind.CALL_ENTITY, kinds)

    def test_seed_tenant_zero_is_idempotent(self):
        from tenancy.models import Organization
        from vault.models import Classification

        call_command("seed_tenant_zero", stdout=StringIO())
        self.assertEqual(Organization.objects.filter(slug="shamma-rohi").count(), 1)
        self.assertEqual(Classification.objects.for_org(self.org.pk).count(), 2)
