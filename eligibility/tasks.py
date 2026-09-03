"""
Background evaluation.

Verdicts are precomputed rather than calculated on request.  Two reasons: a
list endpoint that ran the engine inline would be slow, and -- worse -- the
same URL would return different answers depending on when it was called, which
makes "why did it say that on Tuesday?" unanswerable.
"""
import logging

from celery import shared_task
from django.utils import timezone

from core.tenancy import active_org

logger = logging.getLogger(__name__)


@shared_task
def evaluate_tender_for_org(organization_id: int, tender_id: int) -> dict:
    """Evaluate one tender for one tenant."""
    from eligibility.engine import evaluate
    from tenancy.models import Organization
    from tenders.models import Tender

    organization = Organization.objects.get(pk=organization_id)
    tender = Tender.objects.get(pk=tender_id)
    with active_org(organization):
        check = evaluate(organization, tender)
    return {"check_id": check.pk, "verdict": check.verdict}


@shared_task
def evaluate_open_tenders_for_org(organization_id: int) -> dict:
    """
    Evaluate every open tender for one tenant.

    Skips tenders whose inputs digest is unchanged since the last check, so a
    nightly pass over a tenant who uploaded nothing costs almost nothing.
    """
    from eligibility.engine import _inputs_digest, evaluate
    from eligibility.models import EligibilityCheck
    from tenancy.models import Organization
    from tenders.models import Tender

    organization = Organization.objects.get(pk=organization_id)
    now = timezone.now()
    open_tenders = Tender.objects.filter(closing_at__gte=now, is_cancelled=False)

    latest_digests = {}
    for tender_id, digest, computed_at in (
        EligibilityCheck.objects.for_org(organization_id)
        .order_by("tender_id", "-computed_at")
        .values_list("tender_id", "inputs_digest", "computed_at")
    ):
        current = latest_digests.get(tender_id)
        if current is None or computed_at > current[1]:
            latest_digests[tender_id] = (digest, computed_at)

    evaluated = skipped = 0
    verdicts: dict = {}
    with active_org(organization):
        for tender in open_tenders.iterator():
            previous = latest_digests.get(tender.pk)
            if previous and previous[0] == _inputs_digest(organization, tender):
                skipped += 1
                continue
            check = evaluate(organization, tender, now=now)
            verdicts[check.verdict] = verdicts.get(check.verdict, 0) + 1
            evaluated += 1

    return {
        "organization_id": organization_id,
        "evaluated": evaluated,
        "skipped_unchanged": skipped,
        "verdicts": verdicts,
    }


@shared_task
def evaluate_all_tenants() -> dict:
    """Nightly fan-out, one task per active tenant."""
    from tenancy.models import Organization

    org_ids = list(
        Organization.objects.filter(is_active=True).values_list("pk", flat=True)
    )
    for org_id in org_ids:
        evaluate_open_tenders_for_org.delay(org_id)
    return {"tenants_queued": len(org_ids)}


@shared_task
def raise_action_requests_for_unknowns(organization_id: int) -> dict:
    """
    Turn UNKNOWN findings into work a person can actually do.

    An UNKNOWN that nobody is asked to resolve is just a silent screening
    failure with extra steps, so every one becomes an ActionRequest carrying
    the finding's reason and remedy as its brief.
    """
    from bids.models import ActionRequest
    from core.enums import Verdict
    from eligibility.models import RequirementFinding
    from tenancy.models import Organization

    organization = Organization.objects.get(pk=organization_id)
    created = 0
    with active_org(organization):
        findings = (
            RequirementFinding.objects.for_org(organization_id)
            .filter(verdict=Verdict.UNKNOWN)
            .select_related("eligibility_check", "eligibility_check__tender", "spec")
        )
        for finding in findings:
            tender = finding.eligibility_check.tender
            if tender.closing_at and tender.closing_at < timezone.now():
                continue
            kind = (
                ActionRequest.Kind.VERIFY_CLASSIFICATION
                if finding.code.startswith("CLASSIFICATION")
                else ActionRequest.Kind.RESOLVE_UNKNOWN
            )
            _, was_created = ActionRequest.objects.get_or_create(
                organization=organization,
                finding=finding,
                defaults={
                    "kind": kind,
                    "status": ActionRequest.Status.OPEN,
                    "title": f"Resolve: {finding.get_code_display()}",
                    "tender": tender,
                    "due_at": tender.closing_at,
                    "priority": 50,
                    "brief": "\n\n".join(
                        part for part in [
                            f"Tender: {tender.reference_number or tender.pk} - {tender.title}",
                            f"Entity: {tender.entity.name}",
                            f"Closes: {tender.closing_at:%d %b %Y %H:%M}"
                            if tender.closing_at else "",
                            f"Finding: {finding.reason}",
                            f"Quoted from the notice: {finding.source_text}"
                            if finding.source_text else "",
                            f"What to do: {finding.remedy}" if finding.remedy else "",
                        ] if part
                    ),
                },
            )
            created += 1 if was_created else 0
    return {"organization_id": organization_id, "action_requests_created": created}
