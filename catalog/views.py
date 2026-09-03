from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters

from catalog.models import (
    ClassificationScheme,
    CredentialAuthority,
    Discipline,
    District,
    FinancialClassCap,
    ProcurementDomain,
    ProcuringEntity,
    Region,
    RequirementSpec,
)
from catalog.serializers import (
    ClassificationSchemeSerializer,
    CredentialAuthoritySerializer,
    DisciplineSerializer,
    DistrictSerializer,
    FinancialClassCapSerializer,
    ProcurementDomainSerializer,
    ProcuringEntitySerializer,
    RegionSerializer,
    RequirementSpecSerializer,
)
from core.viewsets import CatalogReadOnlyViewSet


class RegionViewSet(CatalogReadOnlyViewSet):
    queryset = Region.objects.all()
    serializer_class = RegionSerializer
    filterset_fields = ("code", "district_seed_complete")


class DistrictViewSet(CatalogReadOnlyViewSet):
    queryset = District.objects.select_related("region")
    serializer_class = DistrictSerializer
    filterset_fields = ("region", "region__code", "kind")
    search_fields = ("name", "capital")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]


class ClassificationSchemeViewSet(CatalogReadOnlyViewSet):
    queryset = ClassificationScheme.objects.prefetch_related("categories")
    serializer_class = ClassificationSchemeSerializer


class FinancialClassCapViewSet(CatalogReadOnlyViewSet):
    queryset = FinancialClassCap.objects.select_related("scheme", "category")
    serializer_class = FinancialClassCapSerializer
    filterset_fields = ("scheme", "scheme__code", "financial_class", "is_stale")


class RequirementSpecViewSet(CatalogReadOnlyViewSet):
    queryset = RequirementSpec.objects.select_related("authority")
    serializer_class = RequirementSpecSerializer
    filterset_fields = ("validity_mode", "is_conditional", "authority__code")
    search_fields = ("code", "name", "doc_type")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]


class CredentialAuthorityViewSet(CatalogReadOnlyViewSet):
    queryset = CredentialAuthority.objects.all()
    serializer_class = CredentialAuthoritySerializer


class ProcurementDomainViewSet(CatalogReadOnlyViewSet):
    queryset = ProcurementDomain.objects.all()
    serializer_class = ProcurementDomainSerializer
    filterset_fields = ("object_type",)


class DisciplineViewSet(CatalogReadOnlyViewSet):
    queryset = Discipline.objects.select_related("domain")
    serializer_class = DisciplineSerializer
    filterset_fields = ("domain", "domain__code")
    search_fields = ("name", "slug")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]


class ProcuringEntityViewSet(CatalogReadOnlyViewSet):
    """
    The entity dossier: contacts, publishable PayerScores, recent awards.

    Prefetches are not an optimisation here. Without them the dossier is three
    queries per contact and per award, and this is the endpoint a contractor
    opens before making a phone call.
    """

    queryset = ProcuringEntity.objects.select_related(
        "region", "district", "parent_ministry"
    ).prefetch_related("contacts", "payer_scores", "awards")
    serializer_class = ProcuringEntitySerializer
    filterset_fields = ("channel", "entity_category", "region", "district")
    search_fields = ("name", "ocds_party_id")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
