from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from core.viewsets import TenantReadOnlyViewSet
from eligibility.models import EligibilityCheck
from eligibility.serializers import (
    EligibilityCheckSerializer,
    EligibilitySummarySerializer,
)


class EligibilityCheckViewSet(TenantReadOnlyViewSet):
    """
    Read-only by design.  Checks are produced by the engine and never edited;
    a wrong verdict is fixed by fixing the inputs and re-running, which leaves
    both verdicts on the record.
    """

    queryset = EligibilityCheck.objects.select_related("tender", "tender__entity")
    serializer_class = EligibilityCheckSerializer
    filterset_fields = ("verdict", "tender", "screened_out_reason", "engine_version")
    ordering = ("-computed_at",)

    def get_serializer_class(self):
        if self.action == "list":
            return EligibilitySummarySerializer
        return EligibilityCheckSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action != "list":
            queryset = queryset.prefetch_related("findings", "findings__spec")
        return queryset

    @action(detail=False, methods=["post"])
    def run(self, request, **kwargs):
        """
        Evaluate one tender now, rather than waiting for the nightly pass.

        POST {"tender": <id>}
        """
        from eligibility.engine import evaluate
        from tenancy.models import Organization
        from tenders.models import Tender

        tender_id = request.data.get("tender")
        if not tender_id:
            raise ValidationError({"tender": "This field is required."})
        try:
            tender = Tender.objects.get(pk=tender_id)
        except (Tender.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"tender": "No such tender."})

        organization = Organization.objects.get(pk=self.get_active_org_id())
        check = evaluate(organization, tender)
        serializer = EligibilityCheckSerializer(check, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def needs_human(self, request, **kwargs):
        """
        Every check whose latest verdict is UNKNOWN.

        This is the work queue the product is actually built around: UNKNOWN is
        not a failure state, it is the list of tenders where a five-minute
        phone call might turn into a bid.
        """
        from core.enums import Verdict

        queryset = self.filter_queryset(self.get_queryset()).filter(
            verdict=Verdict.UNKNOWN
        )
        page = self.paginate_queryset(queryset)
        serializer = EligibilitySummarySerializer(
            page if page is not None else queryset, many=True
        )
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)
