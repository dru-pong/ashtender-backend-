"""
Binds the active organization for non-DRF request paths.

A ContextVar rather than thread-local state, so the same code works under
async views and Celery's prefork model without a second implementation.

Scope limit worth being explicit about: this middleware runs *before* DRF
authenticates, so on a token-authenticated API request ``request.user`` is
still anonymous here and no organization can be resolved.  That is why the
authoritative resolution for API requests lives in
``core.viewsets.TenantSafeQuerySetMixin``, which runs after authentication.
This middleware covers session-authenticated paths -- Django admin, the
browsable API, server-rendered views -- and otherwise leaves the context unset.
"""
from core.tenancy import (
    ALL_TENANTS,
    reset_active_org,
    resolve_active_org,
    set_active_org_id,
)


class ActiveOrganizationMiddleware:
    HEADER = "HTTP_X_ASHTENDER_ORG"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        org_id = resolve_active_org(
            getattr(request, "user", None), request.META.get(self.HEADER)
        )
        request.active_org_id = org_id
        token = set_active_org_id(org_id)
        try:
            return self.get_response(request)
        finally:
            # Reset unconditionally: a ContextVar left set would bleed one
            # tenant's scope into the next request handled by this worker.
            reset_active_org(token)
