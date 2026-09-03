"""
PayerScore computation.

The only place in AshTender that deliberately reads across tenants, so the
rules are written here rather than assumed:

*   Only aggregates are stored.  No tenant identifier reaches the PayerScore row.
*   Nothing is published until both floors are met -- enough contracts and
    enough distinct tenants -- because a median over one firm's contracts is
    that firm's confidential payment history with a new label on it.
*   The cross-tenant read announces itself via ``across_tenants(reason=...)``,
    which logs a warning naming this function.
"""
from __future__ import annotations

import math
import statistics

from django.conf import settings
from django.utils import timezone

from receivables.models import IPC, PayerScore


def _percentile(values, fraction: float):
    """
    Nearest-rank percentile.  No numpy: the VPS is small and n is smaller.

    Ceil rather than round, which is the textbook nearest-rank definition and
    avoids Python's banker's rounding turning an exact half into a surprise
    (round(1.5) is 2, not 1).
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(fraction * len(ordered))
    index = min(max(rank - 1, 0), len(ordered) - 1)
    return ordered[index]


def recompute_all(now=None) -> dict:
    """
    Rebuild every PayerScore row.  Nightly job.

    Returns a summary rather than logging only, so the Celery beat task result
    shows how many scores actually became publishable -- which is the number
    that tells you whether the moat is filling.
    """
    now = now or timezone.now()
    floor_contracts = settings.PAYER_SCORE_MIN_CONTRACTS
    floor_tenants = settings.PAYER_SCORE_MIN_TENANTS

    rows = (
        IPC.objects.across_tenants(
            reason="PayerScore aggregation (receivables.payer_score.recompute_all)"
        )
        .select_related("contract")
        .values(
            "organization_id",
            "contract_id",
            "contract__entity_id",
            "contract__funding_source",
            "submitted_on",
            "certified_on",
            "paid_on",
            "status",
        )
    )

    buckets: dict[tuple, dict] = {}
    for row in rows:
        key = (row["contract__entity_id"], row["contract__funding_source"])
        bucket = buckets.setdefault(
            key,
            {"certify": [], "pay": [], "tenants": set(), "contracts": set(),
             "ipcs": 0, "rejected": 0},
        )
        bucket["tenants"].add(row["organization_id"])
        bucket["contracts"].add(row["contract_id"])
        bucket["ipcs"] += 1
        if row["status"] == "REJECTED":
            bucket["rejected"] += 1
        if row["submitted_on"] and row["certified_on"]:
            bucket["certify"].append((row["certified_on"] - row["submitted_on"]).days)
        if row["certified_on"] and row["paid_on"]:
            bucket["pay"].append((row["paid_on"] - row["certified_on"]).days)

    published = suppressed = 0
    for (entity_id, funding_source), bucket in buckets.items():
        contract_count = len(bucket["contracts"])
        tenant_count = len(bucket["tenants"])
        publishable = contract_count >= floor_contracts and tenant_count >= floor_tenants
        PayerScore.objects.update_or_create(
            entity_id=entity_id,
            funding_source=funding_source or "",
            defaults={
                "median_days_to_certify": (
                    int(statistics.median(bucket["certify"])) if bucket["certify"] else None
                ),
                "median_days_to_pay": (
                    int(statistics.median(bucket["pay"])) if bucket["pay"] else None
                ),
                "p90_days_to_pay": _percentile(bucket["pay"], 0.9),
                "rejection_rate": (
                    bucket["rejected"] / bucket["ipcs"] if bucket["ipcs"] else None
                ),
                "contract_count": contract_count,
                "ipc_count": bucket["ipcs"],
                "distinct_tenant_count": tenant_count,
                "is_publishable": publishable,
                "computed_at": now,
            },
        )
        published += 1 if publishable else 0
        suppressed += 0 if publishable else 1

    return {
        "buckets": len(buckets),
        "publishable": published,
        "suppressed_below_floor": suppressed,
        "floor_contracts": floor_contracts,
        "floor_tenants": floor_tenants,
    }
