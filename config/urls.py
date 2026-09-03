"""
API surface, spec section 10.

Two shapes on purpose:

*   ``/api/v1/orgs/{org}/...`` for anything a tenant owns.  The organization is
    in the path, so a request that forgets it does not resolve at all rather
    than quietly falling back to a default tenant.
*   ``/api/v1/tenders|entities|catalog/...`` for global data, which every
    tenant sees identically.

``/api/v1/tenders/?eligible_for_me=true`` is the single crossing point, and it
reads precomputed checks rather than running the engine inline.
"""
from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from bids.views import ActionRequestViewSet, BidTeamViewSet, BidViewSet, InteractionViewSet
from catalog.views import (
    ClassificationSchemeViewSet,
    CredentialAuthorityViewSet,
    DisciplineViewSet,
    DistrictViewSet,
    FinancialClassCapViewSet,
    ProcurementDomainViewSet,
    ProcuringEntityViewSet,
    RegionViewSet,
    RequirementSpecViewSet,
)
from core.views import health
from eligibility.views import EligibilityCheckViewSet
from receivables.views import (
    ArrearsClaimViewSet,
    ContractViewSet,
    IPCViewSet,
    PayerScoreViewSet,
)
from tenders.views import TenderViewSet
from vault.views import (
    ClassificationViewSet,
    DocumentViewSet,
    EquipmentViewSet,
    FinancialYearViewSet,
    KeyPersonnelViewSet,
    LitigationViewSet,
    PastProjectViewSet,
)

# --- tenant-scoped ------------------------------------------------------
# The org id is captured in the router prefix. TenantSafeQuerySetMixin reads it
# from self.kwargs["org_pk"], cross-checks it against the authenticated user's
# active organization, and re-applies the organization filter regardless.
ORG = r"orgs/(?P<org_pk>[^/.]+)"

org_router = DefaultRouter()
org_router.register(f"{ORG}/documents", DocumentViewSet, basename="document")
org_router.register(f"{ORG}/classifications", ClassificationViewSet, basename="classification")
org_router.register(f"{ORG}/financial-years", FinancialYearViewSet, basename="financialyear")
org_router.register(f"{ORG}/past-projects", PastProjectViewSet, basename="pastproject")
org_router.register(f"{ORG}/litigation", LitigationViewSet, basename="litigation")
org_router.register(f"{ORG}/equipment", EquipmentViewSet, basename="equipment")
org_router.register(f"{ORG}/personnel", KeyPersonnelViewSet, basename="keypersonnel")
org_router.register(f"{ORG}/contracts", ContractViewSet, basename="contract")
org_router.register(f"{ORG}/ipcs", IPCViewSet, basename="ipc")
org_router.register(f"{ORG}/arrears-claims", ArrearsClaimViewSet, basename="arrearsclaim")
org_router.register(f"{ORG}/eligibility", EligibilityCheckViewSet, basename="eligibilitycheck")
org_router.register(f"{ORG}/bids", BidViewSet, basename="bid")
org_router.register(f"{ORG}/bid-team", BidTeamViewSet, basename="bidteam")
org_router.register(f"{ORG}/interactions", InteractionViewSet, basename="interaction")
org_router.register(f"{ORG}/action-requests", ActionRequestViewSet, basename="actionrequest")

# --- global -------------------------------------------------------------
global_router = DefaultRouter()
global_router.register("tenders", TenderViewSet, basename="tender")
global_router.register("entities", ProcuringEntityViewSet, basename="procuringentity")
global_router.register("payer-scores", PayerScoreViewSet, basename="payerscore")

catalog_router = DefaultRouter()
catalog_router.register("regions", RegionViewSet, basename="region")
catalog_router.register("districts", DistrictViewSet, basename="district")
catalog_router.register(
    "classification-schemes", ClassificationSchemeViewSet, basename="classificationscheme"
)
catalog_router.register(
    "financial-class-caps", FinancialClassCapViewSet, basename="financialclasscap"
)
catalog_router.register(
    "requirement-specs", RequirementSpecViewSet, basename="requirementspec"
)
catalog_router.register(
    "credential-authorities", CredentialAuthorityViewSet, basename="credentialauthority"
)
catalog_router.register("domains", ProcurementDomainViewSet, basename="procurementdomain")
catalog_router.register("disciplines", DisciplineViewSet, basename="discipline")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", health, name="health"),
    path("api/v1/auth/", include("rest_framework.urls")),
    path("api/v1/", include(org_router.urls)),
    path("api/v1/", include(global_router.urls)),
    path("api/v1/catalog/", include(catalog_router.urls)),
]
