"""
Task tests.  Celery runs eagerly under the test settings, so these exercise the
real code path rather than a mock of it.
"""
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from bids.models import ActionRequest
from core.enums import Verdict
from core.tests.factories import (
    attach_universal,
    hold_class,
    make_category,
    make_document,
    make_org,
    make_spec,
    make_tender,
    require_class,
)
from eligibility.models import EligibilityCheck
from eligibility.tasks import (
    evaluate_all_tenants,
    evaluate_open_tenders_for_org,
    raise_action_requests_for_unknowns,
)


class EvaluateOpenTendersTests(TestCase):
    def setUp(self):
        self.org = make_org()
        self.spec = make_spec()
        attach_universal(self.spec)

    def test_open_tenders_are_evaluated(self):
        make_tender(reference="A-1", days_to_close=30)
        make_tender(reference="A-2", days_to_close=20)
        result = evaluate_open_tenders_for_org(self.org.pk)
        self.assertEqual(result["evaluated"], 2)
        self.assertEqual(EligibilityCheck.objects.for_org(self.org.pk).count(), 2)

    def test_closed_and_cancelled_tenders_are_left_alone(self):
        make_tender(reference="OPEN", days_to_close=30)
        make_tender(reference="CLOSED", days_to_close=-5)
        cancelled = make_tender(reference="CANCELLED", days_to_close=30)
        cancelled.is_cancelled = True
        cancelled.save()
        result = evaluate_open_tenders_for_org(self.org.pk)
        self.assertEqual(result["evaluated"], 1)

    def test_a_second_pass_skips_tenders_whose_inputs_have_not_changed(self):
        """
        A nightly sweep over a tenant who uploaded nothing should cost almost
        nothing, which is what makes precomputing affordable at all.
        """
        make_tender(reference="A-1", days_to_close=30)
        first = evaluate_open_tenders_for_org(self.org.pk)
        second = evaluate_open_tenders_for_org(self.org.pk)
        self.assertEqual(first["evaluated"], 1)
        self.assertEqual(second["evaluated"], 0)
        self.assertEqual(second["skipped_unchanged"], 1)

    def test_uploading_a_document_forces_re_evaluation(self):
        make_tender(reference="A-1", days_to_close=30)
        evaluate_open_tenders_for_org(self.org.pk)
        make_document(
            self.org, self.spec, expires_on=date.today() + timedelta(days=200),
            verified=True,
        )
        result = evaluate_open_tenders_for_org(self.org.pk)
        self.assertEqual(result["evaluated"], 1)
        self.assertEqual(result["skipped_unchanged"], 0)

    def test_verdicts_are_counted_by_kind(self):
        make_tender(reference="A-1", days_to_close=30)
        result = evaluate_open_tenders_for_org(self.org.pk)
        self.assertIn(Verdict.FAIL, result["verdicts"])

    def test_fan_out_queues_only_active_tenants(self):
        other = make_org("Dormant Works Ltd", "dormant")
        other.is_active = False
        other.save()
        self.assertEqual(evaluate_all_tenants()["tenants_queued"], 1)


class ActionRequestFromUnknownTests(TestCase):
    """
    An UNKNOWN nobody is asked to resolve is a silent screening failure with
    extra steps.
    """

    def setUp(self):
        self.org = make_org()
        self.category = make_category(letter="A")

    def _lapsed_classification_tender(self, reference="A-1", days=30):
        tender = make_tender(reference=reference, days_to_close=days)
        require_class(
            tender, self.category, min_class=2,
            source_text="Tenderers shall hold MRH classification A2 or better.",
        )
        hold_class(
            self.org, self.category, financial_class=1,
            valid_from=date(2020, 4, 2), valid_to=date(2022, 4, 2),
        )
        return tender

    def test_an_unknown_finding_becomes_an_action_request(self):
        self._lapsed_classification_tender()
        evaluate_open_tenders_for_org(self.org.pk)
        result = raise_action_requests_for_unknowns(self.org.pk)
        self.assertGreater(result["action_requests_created"], 0)

    def test_a_classification_unknown_is_routed_to_the_ministry(self):
        self._lapsed_classification_tender()
        evaluate_open_tenders_for_org(self.org.pk)
        raise_action_requests_for_unknowns(self.org.pk)
        kinds = set(
            ActionRequest.objects.for_org(self.org.pk).values_list("kind", flat=True)
        )
        self.assertIn(ActionRequest.Kind.VERIFY_CLASSIFICATION, kinds)

    def test_the_brief_carries_the_quote_and_the_remedy(self):
        """The deliverable is the brief, not the flag."""
        self._lapsed_classification_tender()
        evaluate_open_tenders_for_org(self.org.pk)
        raise_action_requests_for_unknowns(self.org.pk)
        request = ActionRequest.objects.for_org(self.org.pk).filter(
            kind=ActionRequest.Kind.VERIFY_CLASSIFICATION
        ).first()
        self.assertIn("A2 or better", request.brief)
        self.assertIn("What to do:", request.brief)
        self.assertIn("Closes:", request.brief)

    def test_running_twice_does_not_duplicate_the_work_queue(self):
        self._lapsed_classification_tender()
        evaluate_open_tenders_for_org(self.org.pk)
        raise_action_requests_for_unknowns(self.org.pk)
        before = ActionRequest.objects.for_org(self.org.pk).count()
        raise_action_requests_for_unknowns(self.org.pk)
        self.assertEqual(ActionRequest.objects.for_org(self.org.pk).count(), before)

    def test_unknowns_on_a_closed_tender_are_not_queued(self):
        """Nobody needs a call brief for a tender that closed last week."""
        tender = self._lapsed_classification_tender(days=30)
        evaluate_open_tenders_for_org(self.org.pk)
        tender.closing_at = timezone.now() - timedelta(days=7)
        tender.save()
        ActionRequest.objects.for_org(self.org.pk).delete()
        result = raise_action_requests_for_unknowns(self.org.pk)
        self.assertEqual(result["action_requests_created"], 0)
