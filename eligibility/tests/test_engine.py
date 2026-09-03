"""
End-to-end engine tests.

Each one asserts a verdict *and* that the finding carries its evidence, because
the spec invariant is that a verdict without a quote and a file does not ship.
"""
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from core.enums import ObjectType, ValidityMode, Verdict
from core.tests.factories import (
    attach_universal,
    complete_five_years,
    hold_class,
    make_cap,
    make_category,
    make_document,
    make_org,
    make_spec,
    make_tender,
    require_class,
)
from core.tenancy import TenantScopeMissing
from eligibility.engine import evaluate, normalise_company_name
from eligibility.models import EligibilityCheck, RequirementFinding
from tenders.models import DebarmentEntry

Code = RequirementFinding.Code


class ScreeningTests(TestCase):
    def test_wrong_object_type_is_screened_out(self):
        org = make_org(object_types=[ObjectType.WORKS])
        tender = make_tender(object_type=ObjectType.GOODS)
        check = evaluate(org, tender)
        self.assertEqual(check.screened_out_reason, "object_type")
        self.assertEqual(check.verdict, Verdict.FAIL)
        self.assertEqual(check.findings.count(), 1)

    def test_too_little_prep_time_is_screened_out(self):
        org = make_org(min_prep_days=7)
        tender = make_tender(days_to_close=2)
        check = evaluate(org, tender)
        self.assertEqual(check.screened_out_reason, "late")
        self.assertEqual(check.findings.get().code, Code.LATE)

    def test_screening_short_circuits_the_document_audit(self):
        """A late tender must not generate twelve document findings."""
        org = make_org(min_prep_days=7)
        spec = make_spec()
        attach_universal(spec)
        tender = make_tender(days_to_close=1)
        check = evaluate(org, tender)
        self.assertEqual(check.findings.count(), 1)


class ClassificationTests(TestCase):
    def setUp(self):
        self.org = make_org()
        self.category = make_category(letter="A")
        self.tender = make_tender()

    def test_no_classification_held_fails_with_the_tender_sentence(self):
        require_class(
            self.tender, self.category, min_class=2,
            source_text="Tenderers must hold MRH classification A2 or better.",
        )
        check = evaluate(self.org, self.tender)
        finding = check.findings.get(code=Code.CLASSIFICATION)
        self.assertEqual(finding.verdict, Verdict.FAIL)
        self.assertIn("A2 or better", finding.source_text)
        self.assertTrue(finding.remedy)

    def test_better_class_passes(self):
        require_class(self.tender, self.category, min_class=3)
        hold_class(self.org, self.category, financial_class=1,
                   valid_from=date(2026, 1, 1), valid_to=date(2027, 1, 1))
        check = evaluate(self.org, self.tender)
        self.assertEqual(check.findings.get(code=Code.CLASSIFICATION).verdict, Verdict.PASS)

    def test_insufficient_class_fails_and_names_what_is_held(self):
        require_class(self.tender, self.category, min_class=2)
        hold_class(self.org, self.category, financial_class=4,
                   valid_from=date(2026, 1, 1), valid_to=date(2027, 1, 1))
        finding = evaluate(self.org, self.tender).findings.get(code=Code.CLASSIFICATION)
        self.assertEqual(finding.verdict, Verdict.FAIL)
        self.assertIn("A4", finding.reason)

    def test_lapsed_classification_is_unknown_not_fail(self):
        """
        Tenant zero's exact situation: A1 on paper, published validity ended
        2 April 2022.  Ministry lists lag, so this must reach a human rather
        than telling a road contractor they cannot bid road work.
        """
        require_class(self.tender, self.category, min_class=2)
        hold_class(self.org, self.category, financial_class=1,
                   valid_from=date(2020, 4, 2), valid_to=date(2022, 4, 2))
        finding = evaluate(self.org, self.tender).findings.get(code=Code.CLASSIFICATION_LAPSED)
        self.assertEqual(finding.verdict, Verdict.UNKNOWN)
        self.assertIn("02 Apr 2022", finding.reason)
        self.assertIn("lag", finding.reason)
        self.assertIsNotNone(finding.satisfied_by_classification)

    def test_missing_validity_window_is_unknown(self):
        require_class(self.tender, self.category, min_class=2)
        hold_class(self.org, self.category, financial_class=1, valid_from=None, valid_to=None)
        finding = evaluate(self.org, self.tender).findings.get(code=Code.CLASSIFICATION_LAPSED)
        self.assertEqual(finding.verdict, Verdict.UNKNOWN)

    def test_unverified_classification_is_unknown(self):
        require_class(self.tender, self.category, min_class=2)
        hold_class(self.org, self.category, financial_class=1, verified=False,
                   valid_from=date(2026, 1, 1), valid_to=date(2027, 1, 1))
        finding = evaluate(self.org, self.tender).findings.get(
            code=Code.CLASSIFICATION_UNVERIFIED
        )
        self.assertEqual(finding.verdict, Verdict.UNKNOWN)
        self.assertIn("register", finding.reason)

    def test_wrong_category_held_still_fails(self):
        """Holding K3 does not help on a tender that demands A2."""
        require_class(self.tender, self.category, min_class=2)
        other = make_category(letter="K", max_class=4)
        hold_class(self.org, other, financial_class=1,
                   valid_from=date(2026, 1, 1), valid_to=date(2027, 1, 1))
        finding = evaluate(self.org, self.tender).findings.get(code=Code.CLASSIFICATION)
        self.assertEqual(finding.verdict, Verdict.FAIL)
        self.assertIn("K1", finding.reason)


class ValueCapTests(TestCase):
    def setUp(self):
        self.org = make_org()
        self.category = make_category(letter="A")

    def test_value_far_over_a_confirmed_cap_fails(self):
        tender = make_tender(value="5000000")
        require_class(tender, self.category, min_class=2)
        hold_class(self.org, self.category, financial_class=2,
                   valid_from=date(2026, 1, 1), valid_to=date(2027, 1, 1))
        make_cap(self.category.scheme, financial_class=2, amount="500000",
                 currency="GHS", is_stale=False)
        finding = evaluate(self.org, tender).findings.get(code=Code.VALUE_CAP)
        self.assertEqual(finding.verdict, Verdict.FAIL)

    def test_value_over_a_stale_cap_is_unknown(self):
        tender = make_tender(value="5000000")
        require_class(tender, self.category, min_class=2)
        hold_class(self.org, self.category, financial_class=2,
                   valid_from=date(2026, 1, 1), valid_to=date(2027, 1, 1))
        make_cap(self.category.scheme, financial_class=2, amount="500000",
                 currency="GHS", is_stale=True)
        check = evaluate(self.org, tender)
        finding = check.findings.filter(
            code__in=[Code.VALUE_CAP, Code.VALUE_CAP_BOUNDARY]
        ).first()
        self.assertEqual(finding.verdict, Verdict.UNKNOWN)

    def test_value_near_the_cap_boundary_is_unknown(self):
        tender = make_tender(value="480000")
        require_class(tender, self.category, min_class=2)
        hold_class(self.org, self.category, financial_class=2,
                   valid_from=date(2026, 1, 1), valid_to=date(2027, 1, 1))
        make_cap(self.category.scheme, financial_class=2, amount="500000",
                 currency="GHS", is_stale=False)
        finding = evaluate(self.org, tender).findings.get(code=Code.VALUE_CAP_BOUNDARY)
        self.assertEqual(finding.verdict, Verdict.UNKNOWN)

    def test_foreign_currency_cap_without_an_fx_rate_is_unknown(self):
        """A USD cap and a GHS tender with no dated rate on file is not a FAIL."""
        tender = make_tender(value="5000000", currency="GHS")
        require_class(tender, self.category, min_class=2)
        hold_class(self.org, self.category, financial_class=2,
                   valid_from=date(2026, 1, 1), valid_to=date(2027, 1, 1))
        make_cap(self.category.scheme, financial_class=2, amount="200000",
                 currency="USD", is_stale=True)
        finding = evaluate(self.org, tender).findings.get(code=Code.VALUE_CAP)
        self.assertEqual(finding.verdict, Verdict.UNKNOWN)
        self.assertIn("exchange rate", finding.reason)


class DocumentRequirementTests(TestCase):
    def setUp(self):
        self.org = make_org()
        self.spec = make_spec("tax-clearance", ValidityMode.PRINTED_EXPIRY)
        attach_universal(self.spec)
        self.tender = make_tender()

    def test_missing_document_fails(self):
        finding = evaluate(self.org, self.tender).findings.get(spec=self.spec)
        self.assertEqual(finding.verdict, Verdict.FAIL)
        self.assertIn("not in the vault", finding.reason)

    def test_valid_verified_document_passes_and_cites_the_file(self):
        doc = make_document(
            self.org, self.spec, expires_on=date.today() + timedelta(days=365),
            verified=True,
        )
        finding = evaluate(self.org, self.tender).findings.get(spec=self.spec)
        self.assertEqual(finding.verdict, Verdict.PASS)
        self.assertEqual(finding.satisfied_by_id, doc.pk)

    def test_expired_document_fails(self):
        make_document(self.org, self.spec, expires_on=date.today() - timedelta(days=10),
                      verified=True)
        finding = evaluate(self.org, self.tender).findings.get(spec=self.spec)
        self.assertEqual(finding.verdict, Verdict.FAIL)

    def test_unverified_extraction_downgrades_pass_to_unknown(self):
        """
        Good enough to screen a tender in, never good enough to assemble a bid
        on.  This is what stops a hallucinated expiry date reaching a submission.
        """
        make_document(
            self.org, self.spec, expires_on=date.today() + timedelta(days=365),
            extracted=True, verified=False,
        )
        finding = evaluate(self.org, self.tender).findings.get(spec=self.spec)
        self.assertEqual(finding.verdict, Verdict.UNKNOWN)
        self.assertIn("not been confirmed by a person", finding.reason)

    def test_human_verified_document_is_preferred_over_extracted_one(self):
        make_document(self.org, self.spec, expires_on=date.today() + timedelta(days=400),
                      extracted=True, verified=False, title="Machine read")
        verified = make_document(
            self.org, self.spec, expires_on=date.today() + timedelta(days=200),
            verified=True, title="Human checked",
        )
        finding = evaluate(self.org, self.tender).findings.get(spec=self.spec)
        self.assertEqual(finding.satisfied_by_id, verified.pk)
        self.assertEqual(finding.verdict, Verdict.PASS)

    def test_conditional_vat_spec_is_excused_for_non_vat_firms(self):
        vat = make_spec("vat-registration", ValidityMode.PERPETUAL, conditional=True)
        attach_universal(vat)
        finding = evaluate(self.org, self.tender).findings.get(spec=vat)
        self.assertEqual(finding.verdict, Verdict.PASS)
        self.assertIn("does not apply", finding.reason)

    def test_conditional_vat_spec_applies_to_vat_registered_firms(self):
        vat = make_spec("vat-registration", ValidityMode.PERPETUAL, conditional=True)
        attach_universal(vat)
        profile = self.org.company_profile
        profile.vat_registered = True
        profile.save()
        finding = evaluate(self.org, self.tender).findings.get(spec=vat)
        self.assertEqual(finding.verdict, Verdict.FAIL)


class FiveYearTests(TestCase):
    def setUp(self):
        self.org = make_org()
        self.tender = make_tender()

    def test_empty_financial_record_fails(self):
        finding = evaluate(self.org, self.tender).findings.get(code=Code.FINANCIAL_YEARS)
        self.assertEqual(finding.verdict, Verdict.FAIL)

    def test_complete_five_years_passes(self):
        year = (timezone.now() + timedelta(days=30)).year
        complete_five_years(self.org, year)
        finding = evaluate(self.org, self.tender).findings.get(code=Code.FINANCIAL_YEARS)
        self.assertEqual(finding.verdict, Verdict.PASS)

    def test_untagged_tender_gives_unknown_on_similar_works(self):
        finding = evaluate(self.org, self.tender).findings.get(code=Code.SIMILAR_WORKS)
        self.assertEqual(finding.verdict, Verdict.UNKNOWN)


class DebarmentTests(TestCase):
    def test_active_debarment_fails_hard_and_stops_further_checks(self):
        org = make_org("Blacklisted Works Ltd", "blacklisted")
        DebarmentEntry.objects.create(
            authority=DebarmentEntry.Authority.WORLD_BANK,
            company_name="Blacklisted Works Limited",
            company_name_normalised=normalise_company_name("Blacklisted Works Ltd"),
            from_date=date(2025, 1, 1), to_date=date(2030, 1, 1),
            reason="Fraudulent practice",
        )
        check = evaluate(org, make_tender())
        self.assertEqual(check.verdict, Verdict.FAIL)
        self.assertEqual(check.screened_out_reason, "debarred")
        self.assertEqual(check.findings.get().code, Code.DEBARMENT)

    def test_expired_debarment_does_not_bar(self):
        org = make_org("Reformed Works Ltd", "reformed")
        DebarmentEntry.objects.create(
            authority=DebarmentEntry.Authority.PPA,
            company_name="Reformed Works Limited",
            company_name_normalised=normalise_company_name("Reformed Works Ltd"),
            from_date=date(2018, 1, 1), to_date=date(2020, 1, 1),
        )
        check = evaluate(org, make_tender())
        self.assertNotEqual(check.screened_out_reason, "debarred")


class NameNormalisationTests(TestCase):
    def test_suffix_variants_collapse(self):
        forms = [
            "Shamma Rohi Limited",
            "Shamma Rohi Ltd.",
            "SHAMMA ROHI COMPANY LIMITED",
            "Shamma Rohi Co. Ltd",
        ]
        self.assertEqual({normalise_company_name(f) for f in forms}, {"SHAMMA ROHI"})

    def test_distinct_firms_stay_distinct(self):
        self.assertNotEqual(
            normalise_company_name("Shamma Rohi Limited"),
            normalise_company_name("Rohi Engineering Limited"),
        )


class ImmutabilityTests(TestCase):
    def test_re_running_creates_a_second_check(self):
        org = make_org()
        tender = make_tender()
        first = evaluate(org, tender)
        second = evaluate(org, tender)
        self.assertNotEqual(first.pk, second.pk)
        # Tender is global, so tender.eligibility_checks spans tenants and the
        # guard is right to refuse it unscoped. Scope to the org instead.
        self.assertEqual(
            EligibilityCheck.objects.for_org(org).filter(tender=tender).count(), 2
        )

    def test_unscoped_reverse_lookup_from_a_global_tender_is_refused(self):
        """The other side of the same coin: a competitor's checks stay invisible."""
        org = make_org()
        tender = make_tender()
        evaluate(org, tender)
        with self.assertRaises(TenantScopeMissing):
            tender.eligibility_checks.count()

    def test_check_records_the_engine_version_and_inputs_digest(self):
        check = evaluate(make_org(), make_tender())
        self.assertTrue(check.engine_version)
        self.assertEqual(len(check.inputs_digest), 64)

    def test_digest_changes_when_a_document_is_verified(self):
        org = make_org()
        spec = make_spec()
        attach_universal(spec)
        doc = make_document(org, spec, expires_on=date(2030, 1, 1), extracted=True)
        tender = make_tender()
        before = evaluate(org, tender).inputs_digest
        doc.verified_by_human = True
        doc.save()
        self.assertNotEqual(evaluate(org, tender).inputs_digest, before)
