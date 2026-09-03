from django_filters import rest_framework as django_filters
from rest_framework import filters
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from django.utils import timezone

from core.viewsets import CatalogReadOnlyViewSet
from tenders.models import Tender
from tenders.search import search_tenders
from tenders.serializers import TenderDetailSerializer, TenderListSerializer


class TenderFilterSet(django_filters.FilterSet):
    closing_after = django_filters.IsoDateTimeFilter(
        field_name="closing_at", lookup_expr="gte"
    )
    closing_before = django_filters.IsoDateTimeFilter(
        field_name="closing_at", lookup_expr="lte"
    )
    min_value = django_filters.NumberFilter(field_name="estimated_value", lookup_expr="gte")
    max_value = django_filters.NumberFilter(field_name="estimated_value", lookup_expr="lte")
    open_only = django_filters.BooleanFilter(method="filter_open_only")

    class Meta:
        model = Tender
        fields = ("object_type", "method", "funding_source", "domain", "region",
                  "district", "entity", "submission_mode", "is_cancelled")

    def filter_open_only(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(closing_at__gte=timezone.now(), is_cancelled=False)


class TenderViewSet(CatalogReadOnlyViewSet):
    """
    Tenders are global; a tender belongs to nobody.

    ``?eligible_for_me=true`` is the one tenant-aware parameter, and it filters
    on precomputed EligibilityCheck rows rather than evaluating inline. Running
    the engine inside a list request would be slow and, worse, would make the
    same URL return different results depending on when it was called.
    """

    queryset = Tender.objects.select_related("entity", "region", "district", "domain")
    filterset_class = TenderFilterSet
    filter_backends = [
        django_filters.DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    search_fields = ("title", "reference_number", "entity__name")
    ordering_fields = ("published_at", "closing_at", "estimated_value")
    ordering = ("-published_at",)

    def get_serializer_class(self):
        return TenderListSerializer if self.action == "list" else TenderDetailSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "retrieve":
            queryset = queryset.prefetch_related(
                "required_classes__category", "required_classes__scheme",
                "requirements__spec", "tender_disciplines__discipline",
                "sightings__source",
            )
        if self._eligible_for_me_requested():
            queryset = self._filter_to_eligible(queryset)
        return queryset

    def _eligible_for_me_requested(self) -> bool:
        raw = self.request.query_params.get("eligible_for_me", "").strip().lower()
        return raw in ("1", "true", "yes")

    def _active_org_id(self):
        org_id = getattr(self.request, "active_org_id", None)
        if org_id is None:
            raise PermissionDenied(
                "eligible_for_me needs an active organization. Send the "
                "X-AshTender-Org header."
            )
        return org_id

    def _latest_verdicts(self, org_id) -> dict:
        """
        This tenant's most recent verdict per tender, in one query.

        Resolved in Python rather than with DISTINCT ON so the same code path
        runs on Postgres and sqlite. The row count is per-tenant and bounded by
        tenders seen, not by tenants, so this stays small.
        """
        from eligibility.models import EligibilityCheck

        rows = (
            EligibilityCheck.objects.for_org(org_id)
            .order_by("tender_id", "-computed_at")
            .values_list("tender_id", "verdict", "computed_at")
        )
        latest: dict = {}
        for tender_id, verdict, computed_at in rows:
            current = latest.get(tender_id)
            if current is None or computed_at > current[1]:
                latest[tender_id] = (verdict, computed_at)
        return {tender_id: verdict for tender_id, (verdict, _) in latest.items()}

    def _filter_to_eligible(self, queryset):
        """
        Restrict to tenders whose most recent check for this tenant is PASS.

        Deliberately excludes UNKNOWN: this list answers "what can I bid right
        now", and an unresolved question is not a yes. UNKNOWN tenders live on
        /api/v1/orgs/{org}/eligibility/needs_human/, which is a different
        question with a different answer.
        """
        from core.enums import Verdict

        verdicts = self._latest_verdicts(self._active_org_id())
        eligible_ids = [
            tender_id for tender_id, verdict in verdicts.items() if verdict == Verdict.PASS
        ]
        return queryset.filter(pk__in=eligible_ids)

    def get_serializer_context(self):
        """
        Attach this tenant's latest verdict per tender, so the list serializer
        never issues a per-row lookup.
        """
        context = super().get_serializer_context()
        org_id = getattr(self.request, "active_org_id", None)
        if org_id is not None:
            context["verdict_by_tender_id"] = self._latest_verdicts(org_id)
        return context

    @action(detail=False, methods=["get"])
    def search(self, request, **kwargs):
        """
        Free-text search.  Postgres uses full-text plus trigram; sqlite falls
        back to substring matching (see tenders/search.py).
        """
        query = request.query_params.get("q", "")
        queryset = search_tenders(self.filter_queryset(self.get_queryset()), query)
        page = self.paginate_queryset(queryset)
        serializer = TenderListSerializer(
            page if page is not None else queryset, many=True,
            context=self.get_serializer_context(),
        )
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)
