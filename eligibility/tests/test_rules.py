"""Unit tests for the pure rule functions.  No fixtures, no database."""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from core.enums import ValidityMode, Verdict
from eligibility.rules import (
    cap_outcome,
    class_is_satisfied,
    document_validity,
    financial_years_outcome,
    prep_time_outcome,
)

CLOSING = date(2026, 6, 30)


def spec(mode, recency=None, name="GRA Tax Clearance Certificate"):
    return SimpleNamespace(
        validity_mode=mode, recency_months=recency, name=name,
        doc_type="TAX_CLEARANCE", authority=SimpleNamespace(name="Ghana Revenue Authority"),
    )


def doc(expires_on=None, issued_on=None):
    return SimpleNamespace(expires_on=expires_on, issued_on=issued_on)


class ClassOrderingTests(SimpleTestCase):
    """
    Class 1 is the highest capacity.  Getting this inversion wrong would screen
    a class-1 firm out of every class-3 tender, so it is tested explicitly.
    """

    def test_better_class_satisfies_lower_requirement(self):
        self.assertTrue(class_is_satisfied(held_class=1, required_class=3))

    def test_exact_class_satisfies(self):
        self.assertTrue(class_is_satisfied(held_class=3, required_class=3))

    def test_worse_class_does_not_satisfy(self):
        self.assertFalse(class_is_satisfied(held_class=4, required_class=3))

    def test_or_better_false_demands_exact_match(self):
        self.assertFalse(class_is_satisfied(1, 3, or_better=False))
        self.assertTrue(class_is_satisfied(3, 3, or_better=False))

    def test_a1_satisfies_a2_or_better(self):
        """Tenant zero's real case: A1B1 against a class-2 road tender."""
        self.assertTrue(class_is_satisfied(held_class=1, required_class=2))


class DocumentValidityTests(SimpleTestCase):
    def test_perpetual_always_passes(self):
        outcome = document_validity(doc(), spec(ValidityMode.PERPETUAL), CLOSING)
        self.assertEqual(outcome.verdict, Verdict.PASS)

    def test_printed_expiry_after_closing_passes(self):
        outcome = document_validity(
            doc(expires_on=date(2026, 12, 31)), spec(ValidityMode.PRINTED_EXPIRY), CLOSING
        )
        self.assertEqual(outcome.verdict, Verdict.PASS)

    def test_printed_expiry_before_closing_fails(self):
        outcome = document_validity(
            doc(expires_on=date(2026, 5, 1)), spec(ValidityMode.PRINTED_EXPIRY), CLOSING
        )
        self.assertEqual(outcome.verdict, Verdict.FAIL)
        self.assertIn("expired", outcome.reason)
        self.assertTrue(outcome.remedy)

    def test_expiry_exactly_on_closing_day_passes(self):
        outcome = document_validity(
            doc(expires_on=CLOSING), spec(ValidityMode.PRINTED_EXPIRY), CLOSING
        )
        self.assertEqual(outcome.verdict, Verdict.PASS)

    def test_missing_expiry_is_unknown_not_fail(self):
        """No recorded date is a data gap, not evidence the document is invalid."""
        outcome = document_validity(doc(), spec(ValidityMode.PRINTED_EXPIRY), CLOSING)
        self.assertEqual(outcome.verdict, Verdict.UNKNOWN)

    def test_issue_recency_within_window_passes(self):
        outcome = document_validity(
            doc(issued_on=CLOSING - timedelta(days=60)),
            spec(ValidityMode.ISSUE_RECENCY, recency=6), CLOSING,
        )
        self.assertEqual(outcome.verdict, Verdict.PASS)

    def test_issue_recency_outside_window_fails(self):
        outcome = document_validity(
            doc(issued_on=CLOSING - timedelta(days=400)),
            spec(ValidityMode.ISSUE_RECENCY, recency=6), CLOSING,
        )
        self.assertEqual(outcome.verdict, Verdict.FAIL)

    def test_annual_renewal_current_year_passes(self):
        outcome = document_validity(
            doc(issued_on=date(2026, 1, 15)), spec(ValidityMode.ANNUAL_RENEWAL), CLOSING
        )
        self.assertEqual(outcome.verdict, Verdict.PASS)

    def test_annual_renewal_prior_year_fails(self):
        outcome = document_validity(
            doc(issued_on=date(2025, 1, 15)), spec(ValidityMode.ANNUAL_RENEWAL), CLOSING
        )
        self.assertEqual(outcome.verdict, Verdict.FAIL)
        self.assertIn("2025", outcome.reason)


class CapOutcomeTests(SimpleTestCase):
    def test_unlimited_cap_passes(self):
        outcome = cap_outcome(Decimal("50000000"), None, 0.15, False)
        self.assertEqual(outcome.verdict, Verdict.PASS)

    def test_comfortably_under_cap_passes(self):
        outcome = cap_outcome(Decimal("100000"), Decimal("500000"), 0.15, False)
        self.assertEqual(outcome.verdict, Verdict.PASS)

    def test_within_boundary_band_is_unknown(self):
        """Spec 3.8: within +/-15% of a cap, emit UNKNOWN, not FAIL."""
        outcome = cap_outcome(Decimal("480000"), Decimal("500000"), 0.15, False)
        self.assertEqual(outcome.verdict, Verdict.UNKNOWN)

    def test_just_over_cap_but_inside_band_is_unknown(self):
        outcome = cap_outcome(Decimal("520000"), Decimal("500000"), 0.15, False)
        self.assertEqual(outcome.verdict, Verdict.UNKNOWN)

    def test_far_over_a_confirmed_cap_fails(self):
        outcome = cap_outcome(Decimal("5000000"), Decimal("500000"), 0.15, False)
        self.assertEqual(outcome.verdict, Verdict.FAIL)

    def test_far_over_a_stale_cap_is_unknown_not_fail(self):
        """A cap nobody has confirmed may never be the sole reason for a FAIL."""
        outcome = cap_outcome(Decimal("5000000"), Decimal("500000"), 0.15, True)
        self.assertEqual(outcome.verdict, Verdict.UNKNOWN)

    def test_missing_tender_value_is_unknown(self):
        outcome = cap_outcome(None, Decimal("500000"), 0.15, False)
        self.assertEqual(outcome.verdict, Verdict.UNKNOWN)


class PrepTimeTests(SimpleTestCase):
    def test_ample_time_passes(self):
        from django.utils import timezone

        now = timezone.now()
        outcome = prep_time_outcome(now + timedelta(days=20), now, 5)
        self.assertEqual(outcome.verdict, Verdict.PASS)

    def test_inside_minimum_window_fails(self):
        from django.utils import timezone

        now = timezone.now()
        outcome = prep_time_outcome(now + timedelta(days=2), now, 5)
        self.assertEqual(outcome.verdict, Verdict.FAIL)

    def test_already_closed_fails(self):
        from django.utils import timezone

        now = timezone.now()
        outcome = prep_time_outcome(now - timedelta(days=3), now, 5)
        self.assertEqual(outcome.verdict, Verdict.FAIL)

    def test_no_closing_date_is_unknown(self):
        from django.utils import timezone

        outcome = prep_time_outcome(None, timezone.now(), 5)
        self.assertEqual(outcome.verdict, Verdict.UNKNOWN)


class FinancialYearsTests(SimpleTestCase):
    @staticmethod
    def years(*specs):
        return [SimpleNamespace(year=year, is_complete=complete) for year, complete in specs]

    def test_five_complete_years_passes(self):
        outcome = financial_years_outcome(
            self.years((2025, True), (2024, True), (2023, True), (2022, True), (2021, True)),
            5, 2026,
        )
        self.assertEqual(outcome.verdict, Verdict.PASS)

    def test_gap_fails_and_names_the_missing_year(self):
        outcome = financial_years_outcome(
            self.years((2025, True), (2024, True), (2022, True), (2021, True)), 5, 2026
        )
        self.assertEqual(outcome.verdict, Verdict.FAIL)
        self.assertIn("2023", outcome.reason)

    def test_year_present_without_audited_accounts_is_still_missing(self):
        outcome = financial_years_outcome(
            self.years((2025, True), (2024, False), (2023, True), (2022, True), (2021, True)),
            5, 2026,
        )
        self.assertEqual(outcome.verdict, Verdict.FAIL)
        self.assertIn("2024", outcome.reason)
        self.assertIn("without audited accounts", outcome.reason)

    def test_no_records_at_all_lists_every_year(self):
        outcome = financial_years_outcome([], 5, 2026)
        self.assertEqual(outcome.verdict, Verdict.FAIL)
        for year in (2021, 2022, 2023, 2024, 2025):
            self.assertIn(str(year), outcome.reason)
