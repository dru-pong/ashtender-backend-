"""
DRF bases.  Spec section 10: "Tenant-scoped endpoints filter BEFORE searching,
never after."
"""
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied

from core.tenancy import (
    ALL_TENANTS,
    TenantOwned,
    TenantScopeMissing,
    allowed_org_ids,
    resolve_active_org,
)

HEADER = "HTTP_X_ASHTENDER_ORG"


class TenantSafeQuerySetMixin:
    """
    Belt to the queryset's braces.

    ``TenantQuerySet`` already refuses to evaluate unscoped, which catches the
    mistake at runtime.  This mixin catches it one layer earlier -- before DRF
    filters, paginates or serializes -- so a forgotten scope shows up as a
    failing test rather than as a query that happens not to be evaluated on
    the code path the test exercised.

    It is also where the *authoritative* organization resolution happens, for a
    mechanical reason: Django middleware runs before DRF authenticates, so on a
    token-authenticated request the middleware sees an anonymous user and can
    resolve nothing.  Anything that trusted the middleware's answer on an API
    request would be trusting a value that is always None there.
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        model = getattr(queryset, "model", None)
        if model is not None and issubclass(model, TenantOwned):
            org_id = self.get_active_org_id()
            if org_id is None:
                raise PermissionDenied("No active organization for this request.")
            scope = getattr(queryset, "tenant_scope", None)
            if scope == ALL_TENANTS:
                # across_tenants() is for the PayerScore job and the admin.
                # Whatever the reason, its rows are never served over the API.
                raise TenantScopeMissing(
                    f"{model.__name__} queryset was built with across_tenants() "
                    "and cannot be served through a tenant endpoint."
                )
            if scope != org_id:
                # Covers three cases, all handled the same way: never scoped,
                # scoped by a parent row (single-tenant, but which tenant is
                # decided by an attacker-supplied pk), or scoped to a different
                # organization entirely. Re-filtering on organization_id is
                # idempotent, so doing it unconditionally costs nothing and
                # makes the org filter the last word.
                queryset = queryset.for_org(org_id)
        return queryset

    def get_active_org_id(self):
        """
        The organization this request may act for, or None.

        Resolution is membership-gated: the URL segment and the header are both
        treated as requests, never as grants. If they disagree, or name an
        organization the user does not belong to, the answer is None and the
        caller raises. Cached on the request because get_queryset and
        get_serializer_context both ask.
        """
        cached = getattr(self.request, "_ashtender_org_id", "unset")
        if cached != "unset":
            return cached

        user = getattr(self.request, "user", None)
        permitted = allowed_org_ids(user)
        header = self.request.META.get(HEADER)
        url_org = self.kwargs.get("org_pk") or self.kwargs.get("org")

        org_id = None
        if url_org is not None:
            org_id = resolve_active_org(user, url_org, allowed=permitted)
            if org_id is not None and header not in (None, ""):
                # A header naming a different organization than the URL is a
                # confused or malicious client either way. Refuse rather than
                # pick one.
                from_header = resolve_active_org(user, header, allowed=permitted)
                if from_header != org_id:
                    org_id = None
        else:
            org_id = resolve_active_org(user, header, allowed=permitted)

        self.request._ashtender_org_id = org_id
        self.request.active_org_id = org_id
        return org_id

    def perform_create(self, serializer):
        model = getattr(getattr(self, "queryset", None), "model", None)
        if model is not None and issubclass(model, TenantOwned):
            org_id = self.get_active_org_id()
            if org_id is None:
                raise PermissionDenied("No active organization for this request.")
            # organization comes from the resolved context, never from the
            # payload, so a client cannot write into another tenant's vault.
            serializer.save(organization_id=org_id)
        else:
            serializer.save()


class TenantModelViewSet(TenantSafeQuerySetMixin, viewsets.ModelViewSet):
    pass


class TenantReadOnlyViewSet(TenantSafeQuerySetMixin, viewsets.ReadOnlyModelViewSet):
    pass


class CatalogReadOnlyViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Global reference data.  No tenant, read-only for everyone.

    ``active_org_id`` is still resolved and cached on the request, because some
    global endpoints take an optional tenant-aware parameter -- notably
    /api/v1/tenders/?eligible_for_me=true -- and they must not fall back to the
    middleware's answer, which is always None under token authentication.
    """

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        request.active_org_id = resolve_active_org(
            getattr(request, "user", None), request.META.get(HEADER)
        )
