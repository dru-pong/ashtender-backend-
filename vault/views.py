from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone

from core.viewsets import TenantModelViewSet
from vault.models import (
    Classification,
    Document,
    Equipment,
    FinancialYear,
    KeyPersonnel,
    Litigation,
    PastProject,
)
from vault.serializers import (
    ClassificationSerializer,
    DocumentSerializer,
    EquipmentSerializer,
    FinancialYearSerializer,
    KeyPersonnelSerializer,
    LitigationSerializer,
    PastProjectSerializer,
)


class DocumentViewSet(TenantModelViewSet):
    queryset = Document.objects.select_related("spec", "issuing_authority")
    serializer_class = DocumentSerializer
    filterset_fields = ("spec", "spec__code", "extracted", "verified_by_human")
    search_fields = ("title", "reference_number")
    ordering_fields = ("created_at", "expires_on", "issued_on")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]

    def perform_create(self, serializer):
        """
        Save the upload, then queue processing: hash -> render pages -> extract.

        The organization is applied by the mixin's perform_create from the
        resolved request context, never the payload, so a client cannot write
        into another tenant's vault. Processing is queued (not run inline) so
        the upload response is fast; ``content_hash`` and pages appear shortly
        after via the worker. ``verified_by_human`` is untouched here -- only
        the confirm action sets it.
        """
        super().perform_create(serializer)
        document = serializer.instance
        if document.file:
            from vault.tasks import process_uploaded_document

            process_uploaded_document.delay(document.pk, document.organization_id)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None, **kwargs):
        """
        Record that a person checked the extracted dates against the crop.

        The only way ``verified_by_human`` is ever set. It is not a field a
        client can PATCH, because the whole point is that it means someone
        looked -- and that claim has to be attributable.
        """
        document = self.get_object()
        document.verified_by_human = True
        document.verified_by = request.user
        document.verified_at = timezone.now()
        document.save(update_fields=["verified_by_human", "verified_by", "verified_at"])
        return Response(self.get_serializer(document).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"])
    def expiring(self, request, **kwargs):
        """
        Documents expiring within N days (default 60).

        This is the query behind the renewal nudge, which is the feature that
        keeps a vault current instead of becoming a folder of expired PDFs.
        """
        from datetime import timedelta

        try:
            days = int(request.query_params.get("days", 60))
        except (TypeError, ValueError):
            return Response(
                {"detail": "days must be an integer."}, status=status.HTTP_400_BAD_REQUEST
            )
        horizon = timezone.now().date() + timedelta(days=days)
        queryset = self.filter_queryset(self.get_queryset()).filter(
            expires_on__isnull=False, expires_on__lte=horizon, superseded_by__isnull=True
        ).order_by("expires_on")
        return Response(self.get_serializer(queryset, many=True).data)


class ClassificationViewSet(TenantModelViewSet):
    queryset = Classification.objects.select_related("scheme", "category", "certificate")
    serializer_class = ClassificationSerializer
    filterset_fields = ("scheme", "scheme__code", "category", "category__letter")


class FinancialYearViewSet(TenantModelViewSet):
    queryset = FinancialYear.objects.select_related("audited_accounts")
    serializer_class = FinancialYearSerializer
    filterset_fields = ("year",)
    ordering = ("-year",)


class PastProjectViewSet(TenantModelViewSet):
    queryset = PastProject.objects.select_related(
        "client_entity", "district", "completion_cert"
    ).prefetch_related("disciplines")
    serializer_class = PastProjectSerializer
    filterset_fields = ("is_ongoing", "client_entity", "district", "role")
    search_fields = ("name", "client_name")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]


class LitigationViewSet(TenantModelViewSet):
    queryset = Litigation.objects.all()
    serializer_class = LitigationSerializer
    filterset_fields = ("year", "is_resolved")


class EquipmentViewSet(TenantModelViewSet):
    queryset = Equipment.objects.all()
    serializer_class = EquipmentSerializer
    filterset_fields = ("ownership",)


class KeyPersonnelViewSet(TenantModelViewSet):
    queryset = KeyPersonnel.objects.all()
    serializer_class = KeyPersonnelSerializer
    search_fields = ("full_name", "position")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
