"""Nightly receivables jobs."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def recompute_payer_scores() -> dict:
    """
    Rebuild every PayerScore.

    The number worth watching in the return value is ``publishable``: it is how
    much of the aggregate has cleared the minimum-n floor, which is the same as
    asking how much of the moat actually exists yet.
    """
    from receivables.payer_score import recompute_all

    summary = recompute_all()
    logger.info("payer scores recomputed: %s", summary)
    return summary


@shared_task
def flag_stalled_ipcs(days: int = 90) -> dict:
    """
    Raise an ActionRequest for certificates that have sat too long.

    AshTender documents and tracks claims. It does not chase payment, and this
    task is careful to stay on that side of the line: it tells the contractor
    what is stuck and for how long, and leaves the decision about who to call
    to them.
    """
    from datetime import date, timedelta

    from bids.models import ActionRequest
    from core.tenancy import active_org
    from receivables.models import IPC
    from tenancy.models import Organization

    cutoff = date.today() - timedelta(days=days)
    created = 0
    for organization in Organization.objects.filter(is_active=True):
        with active_org(organization):
            stalled = (
                IPC.objects.for_org(organization.pk)
                .filter(paid_on__isnull=True)
                .select_related("contract", "contract__entity")
            )
            for ipc in stalled:
                anchor = ipc.certified_on or ipc.submitted_on
                if anchor is None or anchor > cutoff:
                    continue
                age = (date.today() - anchor).days
                _, was_created = ActionRequest.objects.get_or_create(
                    organization=organization,
                    kind=ActionRequest.Kind.CALL_ENTITY,
                    title=f"IPC {ipc.number} on {ipc.contract.name} is {age} days old",
                    defaults={
                        "status": ActionRequest.Status.OPEN,
                        "priority": 40,
                        "brief": (
                            f"Entity: {ipc.contract.entity.name}\n"
                            f"Funding source: {ipc.contract.funding_source}\n"
                            f"Amount outstanding: {ipc.currency} "
                            f"{ipc.amount_outstanding:,.2f}\n"
                            f"Stuck at: {ipc.get_status_display()} since "
                            f"{anchor:%d %b %Y} ({age} days)\n\n"
                            "This is a status record, not a demand. What it "
                            "supports is a factual enquiry about where the "
                            "certificate currently sits."
                        ),
                    },
                )
                created += 1 if was_created else 0
    return {"days": days, "action_requests_created": created}
