"""
PayerScore tests.

The floor is the point.  A median computed over one firm's contracts is that
firm's confidential payment history with an aggregate label on it, and
publishing it would be a breach dressed as a feature.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings

from core.enums import FundingSource, IPCStatus
from core.tests.factories import make_entity, make_org
from receivables.models import IPC, Contract, PayerScore
from receivables.payer_score import _percentile, recompute_all


def make_contract(org, entity, funding_source=FundingSource.GOG, name=None):
    return Contract.objects.create(
        organization=org, entity=entity, funding_source=funding_source,
        name=name or f"Contract for {org.slug}", value=Decimal("1000000"),
        award_date=date(2024, 1, 15),
    )


def make_ipc(contract, number, submitted=None, certified=None, paid=None,
             status=IPCStatus.SUBMITTED):
    return IPC.objects.create(
        organization_id=contract.organization_id, contract=contract, number=number,
        amount_claimed=Decimal("100000"), amount_certified=Decimal("100000"),
        submitted_on=submitted, certified_on=certified, paid_on=paid, status=status,
    )


class PercentileTests(TestCase):
    def test_nearest_rank(self):
        self.assertEqual(_percentile([10, 20, 30, 40, 50], 0.9), 50)
        self.assertEqual(_percentile([10, 20, 30, 40], 0.5), 20)
        self.assertEqual(_percentile([10, 20, 30, 40, 50], 0.5), 30)

    def test_p90_is_the_ninth_of_ten_not_the_worst_case(self):
        """
        p90 deliberately is not the maximum. One 400-day outlier should not
        become the number a contractor plans cash flow against, but nine
        values at 70 or below should be visible behind it.
        """
        values = [30, 35, 40, 45, 50, 55, 60, 65, 70, 400]
        self.assertEqual(_percentile(values, 0.9), 70)
        self.assertEqual(_percentile(values, 1.0), 400)

    def test_empty_is_none(self):
        self.assertIsNone(_percentile([], 0.9))


@override_settings(PAYER_SCORE_MIN_CONTRACTS=5, PAYER_SCORE_MIN_TENANTS=3)
class PublicationFloorTests(TestCase):
    def setUp(self):
        self.entity = make_entity()

    def test_one_tenant_alone_is_computed_but_never_published(self):
        org = make_org("Solo Works Ltd", "solo")
        for index in range(6):
            contract = make_contract(org, self.entity, name=f"Contract {index}")
            make_ipc(
                contract, "1",
                submitted=date(2024, 1, 1), certified=date(2024, 2, 1),
                paid=date(2024, 6, 1), status=IPCStatus.PAID,
            )
        summary = recompute_all()
        score = PayerScore.objects.get(entity=self.entity)
        self.assertFalse(score.is_publishable)
        self.assertEqual(score.distinct_tenant_count, 1)
        self.assertEqual(summary["suppressed_below_floor"], 1)
        self.assertEqual(summary["publishable"], 0)

    def test_enough_tenants_but_too_few_contracts_is_not_published(self):
        for index in range(3):
            org = make_org(f"Firm {index} Ltd", f"firm-{index}")
            contract = make_contract(org, self.entity, name=f"Contract {index}")
            make_ipc(
                contract, "1", submitted=date(2024, 1, 1),
                certified=date(2024, 2, 1), paid=date(2024, 5, 1),
            )
        recompute_all()
        score = PayerScore.objects.get(entity=self.entity)
        self.assertEqual(score.distinct_tenant_count, 3)
        self.assertEqual(score.contract_count, 3)
        self.assertFalse(score.is_publishable)

    def test_both_floors_met_publishes(self):
        for index in range(5):
            org = make_org(f"Firm {index} Ltd", f"firm-{index}")
            contract = make_contract(org, self.entity, name=f"Contract {index}")
            make_ipc(
                contract, "1", submitted=date(2024, 1, 1),
                certified=date(2024, 1, 31), paid=date(2024, 4, 30),
            )
        summary = recompute_all()
        score = PayerScore.objects.get(entity=self.entity)
        self.assertTrue(score.is_publishable)
        self.assertEqual(score.distinct_tenant_count, 5)
        self.assertEqual(summary["publishable"], 1)


@override_settings(PAYER_SCORE_MIN_CONTRACTS=1, PAYER_SCORE_MIN_TENANTS=1)
class AggregationTests(TestCase):
    def setUp(self):
        self.entity = make_entity()
        self.org = make_org("Alpha Works Ltd", "alpha")

    def test_median_days_are_computed_from_the_real_date_pairs(self):
        contract = make_contract(self.org, self.entity)
        make_ipc(
            contract, "1", submitted=date(2024, 1, 1),
            certified=date(2024, 1, 31), paid=date(2024, 3, 1),
        )
        make_ipc(
            contract, "2", submitted=date(2024, 2, 1),
            certified=date(2024, 3, 2), paid=date(2024, 5, 1),
        )
        recompute_all()
        score = PayerScore.objects.get(entity=self.entity)
        # certify: 1 Jan -> 31 Jan is 30 days, 1 Feb -> 2 Mar is 30 (2024 is a
        # leap year), so the median is 30.
        self.assertEqual(score.median_days_to_certify, 30)
        # pay: 31 Jan -> 1 Mar is 30 days, 2 Mar -> 1 May is 60, median 45.
        self.assertEqual(score.median_days_to_pay, 45)

    def test_incomplete_date_pairs_are_skipped_not_guessed(self):
        contract = make_contract(self.org, self.entity)
        make_ipc(contract, "1", submitted=date(2024, 1, 1))
        recompute_all()
        score = PayerScore.objects.get(entity=self.entity)
        self.assertIsNone(score.median_days_to_certify)
        self.assertIsNone(score.median_days_to_pay)
        self.assertEqual(score.ipc_count, 1)

    def test_funding_source_is_scored_separately(self):
        """
        The whole point: a Road Fund job and a donor job at the same ministry
        are different propositions, and averaging them hides that.
        """
        gog = make_contract(self.org, self.entity, FundingSource.GOG, "GoG job")
        donor = make_contract(self.org, self.entity, FundingSource.IFI, "Donor job")
        make_ipc(gog, "1", submitted=date(2024, 1, 1), certified=date(2024, 2, 1),
                 paid=date(2025, 2, 1))
        make_ipc(donor, "1", submitted=date(2024, 1, 1), certified=date(2024, 2, 1),
                 paid=date(2024, 4, 1))
        recompute_all()
        self.assertEqual(PayerScore.objects.count(), 2)
        slow = PayerScore.objects.get(funding_source=FundingSource.GOG)
        fast = PayerScore.objects.get(funding_source=FundingSource.IFI)
        self.assertGreater(slow.median_days_to_pay, fast.median_days_to_pay)

    def test_rejection_rate_is_tracked(self):
        contract = make_contract(self.org, self.entity)
        make_ipc(contract, "1", status=IPCStatus.REJECTED)
        make_ipc(contract, "2", status=IPCStatus.PAID)
        recompute_all()
        self.assertEqual(PayerScore.objects.get(entity=self.entity).rejection_rate, 0.5)

    def test_recompute_is_idempotent(self):
        contract = make_contract(self.org, self.entity)
        make_ipc(contract, "1", submitted=date(2024, 1, 1), certified=date(2024, 2, 1))
        recompute_all()
        recompute_all()
        self.assertEqual(PayerScore.objects.filter(entity=self.entity).count(), 1)

    def test_the_cross_tenant_read_announces_itself(self):
        contract = make_contract(self.org, self.entity)
        make_ipc(contract, "1", submitted=date(2024, 1, 1))
        with self.assertLogs("core.tenancy", level="WARNING") as captured:
            recompute_all()
        self.assertIn("cross-tenant query", captured.output[0])
        self.assertIn("PayerScore aggregation", captured.output[0])


class IPCAgingTests(TestCase):
    """
    Aging is the product feature: which step a claim is stuck on determines
    what the contractor does about it.
    """

    def setUp(self):
        self.org = make_org()
        self.entity = make_entity()
        self.contract = make_contract(self.org, self.entity)

    def test_days_outstanding_anchors_on_certification_when_certified(self):
        ipc = make_ipc(
            self.contract, "1", submitted=date(2024, 1, 1), certified=date(2024, 3, 1)
        )
        self.assertEqual(ipc.days_outstanding(as_of=date(2024, 4, 1)), 31)

    def test_days_outstanding_anchors_on_submission_when_uncertified(self):
        ipc = make_ipc(self.contract, "1", submitted=date(2024, 1, 1))
        self.assertEqual(ipc.days_outstanding(as_of=date(2024, 2, 1)), 31)

    def test_a_paid_ipc_is_not_outstanding(self):
        ipc = make_ipc(
            self.contract, "1", submitted=date(2024, 1, 1),
            certified=date(2024, 2, 1), paid=date(2024, 3, 1), status=IPCStatus.PAID,
        )
        self.assertIsNone(ipc.days_outstanding(as_of=date(2024, 4, 1)))

    def test_an_ipc_with_no_dates_has_no_age(self):
        """Tenant zero's placeholder: a claim known to exist with no dates on file."""
        ipc = make_ipc(self.contract, "AG-2024-CLAIM")
        self.assertIsNone(ipc.days_outstanding(as_of=date(2026, 1, 1)))

    def test_contract_outstanding_is_certified_less_paid(self):
        make_ipc(self.contract, "1", paid=date(2024, 3, 1))
        ipc = IPC.objects.for_org(self.org.pk).get(number="1")
        ipc.amount_paid = Decimal("40000")
        ipc.save()
        contract = Contract.objects.for_org(self.org.pk).get()
        self.assertEqual(contract.total_certified, Decimal("100000"))
        self.assertEqual(contract.total_paid, Decimal("40000"))
        self.assertEqual(contract.outstanding, Decimal("60000"))
