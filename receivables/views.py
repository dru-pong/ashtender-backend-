from datetime import date

from django.db.models import Sum
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters
from rest_framework.decorators import action
from rest_framework.response import Response

from core.viewsets import CatalogReadOnlyViewSet, TenantModelViewSet
from receivables.models import IPC, ArrearsClaim, Contract, PayerScore
from receivables.serializers import (
    ArrearsClaimSerializer,
    ContractSerializer,
    IPCSerializer,
    PayerScoreSerializer,
)


class ContractViewSet(TenantModelViewSet):
    queryset = Contract.objects.select_related("entity", "district", "tender")
    serializer_class = ContractSerializer
    filterset_fields = ("funding_source", "entity", "is_complete", "district")
    search_fields = ("name", "contract_number", "ppa_reference")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    ordering_fields = ("award_date", "value")


class IPCViewSet(TenantModelViewSet):
    queryset = IPC.objects.select_related("contract", "contract__entity")
    serializer_class = IPCSerializer
    filterset_fields = ("contract", "status", "contract__funding_source")
    ordering_fields = ("submitted_on", "certified_on", "paid_on")

    @action(detail=False, methods=["get"])
    def aging(self, request, **kwargs):
        """
        Unpaid certificates bucketed by how long they have been sitting.

        Bucketed by *where* the claim is stuck, not just how long, because a
        certificate stuck at CERTIFIED is a Treasury problem and one stuck at
        SUBMITTED is an engineer's desk problem, and the contractor does
        completely different things about each.
        """
        as_of = date.today()
        buckets = {
            "0-30": [], "31-60": [], "61-90": [], "91-180": [],
            "181-365": [], "over-365": [], "no-dates": [],
        }
        totals = {key: 0 for key in buckets}

        queryset = self.filter_queryset(self.get_queryset()).filter(paid_on__isnull=True)
        for ipc in queryset:
            days = ipc.days_outstanding(as_of=as_of)
            if days is None:
                key = "no-dates"
            elif days <= 30:
                key = "0-30"
            elif days <= 60:
                key = "31-60"
            elif days <= 90:
                key = "61-90"
            elif days <= 180:
                key = "91-180"
            elif days <= 365:
                key = "181-365"
            else:
                key = "over-365"
            buckets[key].append(
                {
                    "id": ipc.pk,
                    "number": ipc.number,
                    "contract": ipc.contract.name,
                    "entity": ipc.contract.entity.name,
                    "funding_source": ipc.contract.funding_source,
                    "amount_outstanding": ipc.amount_outstanding,
                    "days_outstanding": days,
                    "stuck_at": ipc.status,
                }
            )
            totals[key] += float(ipc.amount_outstanding or 0)

        return Response(
            {
                "as_of": as_of,
                "totals_by_bucket": totals,
                "total_outstanding": sum(totals.values()),
                "buckets": buckets,
            }
        )


class ArrearsClaimViewSet(TenantModelViewSet):
    queryset = ArrearsClaim.objects.prefetch_related("ipcs")
    serializer_class = ArrearsClaimSerializer
    filterset_fields = ("outcome",)


class PayerScoreViewSet(CatalogReadOnlyViewSet):
    """
    Global aggregate, read-only, and filtered to publishable rows in the
    queryset itself rather than in a serializer.

    Filtering here means an unpublishable row cannot be reached by any
    combination of query parameters, ordering or pk lookup. A serializer-level
    check would still expose the row through a detail URL.
    """

    queryset = PayerScore.objects.select_related("entity").filter(is_publishable=True)
    serializer_class = PayerScoreSerializer
    filterset_fields = ("entity", "funding_source")
    ordering_fields = ("median_days_to_pay", "p90_days_to_pay")
