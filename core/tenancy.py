"""
Row-level tenant isolation.

Spec section 5.1.  Tenants are direct competitors bidding the same tenders, so
isolation is existential, not hygienic.  The design goal is stated there as:
"make the unsafe path impossible, not merely discouraged".

Two layers implement that:

1.  ``TenantQuerySet`` refuses to *evaluate* unless a tenant scope was applied.
    Forgetting ``.for_org()`` raises ``TenantScopeMissing`` at the moment rows
    would be fetched -- not later, in a serializer, on a customer's screen.

2.  ``core.viewsets.TenantSafeQuerySetMixin`` checks the scope before DRF ever
    touches the queryset, so an API bug surfaces as a 500 in CI rather than a
    cross-tenant read in production.

Crossing tenants is legitimate in exactly two places -- the PayerScore
aggregation (spec section 8) and Django admin -- and both must say so out loud
via ``across_tenants(reason=...)``, which logs a warning naming the caller.
"""
from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.db import models

logger = logging.getLogger("core.tenancy")

#: Sentinel meaning "deliberately unscoped".  Never a real Organization pk.
ALL_TENANTS = "__ALL_TENANTS__"

#: Sentinel meaning "pinned to one tenant via a parent row, which one unknown".
#: Single-tenant, so the guard is satisfied, but the pk was not available
#: without a query so it is not recorded.
SCOPED_BY_PARENT = "__SCOPED_BY_PARENT__"

_active_org_id: ContextVar[int | None] = ContextVar("ashtender_active_org_id", default=None)


class TenantScopeMissing(Exception):
    """
    Raised when a TenantOwned queryset is evaluated without a tenant scope.

    This is not a recoverable condition.  It means a code path could have
    returned another company's documents, bid intentions or payment history.
    """


class NoActiveOrganization(Exception):
    """Raised when tenant scoping is requested but no organization is active."""


def set_active_org_id(org_id: int | None):
    """Set the active organization for this context.  Returns the reset token."""
    return _active_org_id.set(org_id)


def get_active_org_id() -> int | None:
    return _active_org_id.get()


def reset_active_org(token) -> None:
    _active_org_id.reset(token)


@contextlib.contextmanager
def active_org(org_or_id):
    """
    Bind an active organization for the duration of a block.

    Used by Celery tasks, management commands and tests, none of which pass
    through the request middleware.
    """
    org_id = getattr(org_or_id, "pk", org_or_id)
    token = set_active_org_id(org_id)
    try:
        yield org_id
    finally:
        reset_active_org(token)


def _strict() -> bool:
    return getattr(settings, "TENANCY_STRICT", True)


def allowed_org_ids(user) -> list:
    """
    Organizations a user is actually a member of.

    The authority for every access decision.  A header or a URL segment is a
    *request* to act for an organization; this is what decides whether that
    request is granted.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return []
    from tenancy.models import Membership

    return list(
        Membership.objects.filter(user=user, is_active=True).values_list(
            "organization_id", flat=True
        )
    )


def resolve_active_org(user, requested=None, allowed=None):
    """
    Which organization this user is acting for, or None.

    ``requested`` is whatever the caller asked for -- an ``X-AshTender-Org``
    header, or the org segment of the URL.  It is honoured only if the user is
    a member.

    With no request and exactly one membership, that membership is used.  With
    no request and two or more, the answer is None: guessing which competitor
    someone meant is the one mistake this system cannot make, so the caller is
    made to say.
    """
    permitted = allowed_org_ids(user) if allowed is None else allowed
    if not permitted:
        return None
    if requested not in (None, ""):
        try:
            requested = int(requested)
        except (TypeError, ValueError):
            return None
        return requested if requested in permitted else None
    if len(permitted) == 1:
        return permitted[0]
    return None


def _parent_org_id(value):
    """
    Tenant id implied by a parent row used as a filter value.

    Falls back to SCOPED_BY_PARENT when the value is a bare pk: the constraint
    is still single-tenant, we simply cannot name which tenant without a query,
    and issuing one here would turn every filter into a database round trip.
    """
    org_id = getattr(value, "organization_id", None)
    return org_id if org_id is not None else SCOPED_BY_PARENT


class TenantQuerySet(models.QuerySet):
    """A queryset that will not hand back rows until it has been scoped."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tenant_scope = None

    # Django rebuilds querysets constantly (filter, exclude, order_by, slicing,
    # prefetch).  Without this the scope would silently evaporate mid-chain and
    # the guard would fire on legitimate code.
    def _clone(self):
        clone = super()._clone()
        clone._tenant_scope = self._tenant_scope
        return clone

    @property
    def tenant_scope(self):
        return self._tenant_scope

    @property
    def is_tenant_scoped(self) -> bool:
        return self._tenant_scope is not None

    # Two kinds of filter genuinely pin a queryset to one tenant, and the guard
    # must not fire on either:
    #
    #   filter(organization=org)   -- the direct case, and what reverse
    #                                 relations from Organization arrive as
    #                                 (org.vault_document_set.all()).
    #   filter(document=doc)       -- an exact filter on a FK to another
    #                                 TenantOwned model. That parent row belongs
    #                                 to exactly one tenant, so its children do
    #                                 too. This is what makes check.findings,
    #                                 contract.ipcs and document.pages work
    #                                 without every caller repeating .for_org().
    #
    # Deliberately narrow: only exact equality on the field itself, never a
    # traversal (document__title=...) and never __in, either of which can span
    # tenants.
    def _filter_or_exclude(self, negate, args, kwargs):
        clone = super()._filter_or_exclude(negate, args, kwargs)
        if not negate and clone._tenant_scope is None:
            for key, value in kwargs.items():
                if key.split("__")[0] == "organization":
                    clone._tenant_scope = value
                    break
                if self._is_tenant_owned_parent(key):
                    clone._tenant_scope = _parent_org_id(value)
                    break
        return clone

    def _is_tenant_owned_parent(self, key: str) -> bool:
        """True when ``key`` names a FK on this model pointing at a TenantOwned model."""
        if "__" in key:
            return False
        name = key[:-3] if key.endswith("_id") else key
        try:
            field = self.model._meta.get_field(name)
        except FieldDoesNotExist:
            return False
        related = getattr(field, "related_model", None)
        return bool(
            getattr(field, "many_to_one", False)
            and related is not None
            and issubclass(related, TenantOwned)
        )

    def for_parent(self, parent) -> "TenantQuerySet":
        """
        Scope a child collection by its tenant-owned parent.

        ``DocumentPage.objects.for_parent(document)`` inherits the parent's
        organization, which is the only tenant a child of it can belong to.
        """
        org_id = getattr(parent, "organization_id", None)
        if org_id is None:
            raise NoActiveOrganization(
                f"for_parent() needs a saved TenantOwned instance, got {parent!r}"
            )
        return self.for_org(org_id)

    def for_org(self, org_or_id) -> "TenantQuerySet":
        """Scope to a single organization.  This is the normal path."""
        org_id = getattr(org_or_id, "pk", org_or_id)
        if org_id is None:
            raise NoActiveOrganization("for_org() called with None")
        clone = self.filter(organization_id=org_id)
        clone._tenant_scope = org_id
        return clone

    def for_active_org(self) -> "TenantQuerySet":
        """Scope to the organization bound by middleware / active_org()."""
        org_id = get_active_org_id()
        if org_id is None:
            raise NoActiveOrganization(
                "No active organization in this context. Use for_org(org), or "
                "wrap the call in core.tenancy.active_org(org)."
            )
        return self.for_org(org_id)

    def for_orgs(self, org_ids) -> "TenantQuerySet":
        """Scope to several organizations, e.g. a JV or a staff console."""
        ids = [getattr(o, "pk", o) for o in org_ids]
        if not ids:
            raise NoActiveOrganization("for_orgs() called with an empty list")
        clone = self.filter(organization_id__in=ids)
        clone._tenant_scope = tuple(sorted(ids))
        return clone

    def across_tenants(self, reason: str) -> "TenantQuerySet":
        """
        Deliberately read every tenant's rows.

        Legitimate callers: PayerScore aggregation (spec section 8, which must
        also honour the minimum-n floor before publishing anything) and Django
        admin.  Anything else is a bug wearing a hat.
        """
        if not reason or not str(reason).strip():
            raise TenantScopeMissing(
                "across_tenants() requires a written reason; it is an audit record."
            )
        logger.warning(
            "cross-tenant query on %s.%s: %s",
            self.model._meta.app_label,
            self.model.__name__,
            reason,
        )
        clone = self._chain()
        clone._tenant_scope = ALL_TENANTS
        return clone

    # --- the guard ------------------------------------------------------
    def _assert_scoped(self):
        if self._tenant_scope is None and _strict():
            raise TenantScopeMissing(
                f"{self.model._meta.app_label}.{self.model.__name__} queryset was "
                "evaluated without a tenant scope. Call .for_org(org), "
                ".for_active_org(), or .across_tenants(reason='...') if crossing "
                "tenants is genuinely intended."
            )

    def _fetch_all(self):
        self._assert_scoped()
        super()._fetch_all()

    # Aggregates and existence checks bypass _fetch_all, so they need the guard
    # too -- a leaked count is still a leak.
    def count(self):
        self._assert_scoped()
        return super().count()

    def exists(self):
        self._assert_scoped()
        return super().exists()

    def aggregate(self, *args, **kwargs):
        self._assert_scoped()
        return super().aggregate(*args, **kwargs)

    def delete(self):
        self._assert_scoped()
        return super().delete()

    def update(self, **kwargs):
        self._assert_scoped()
        return super().update(**kwargs)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Default manager for TenantOwned models."""

    use_in_migrations = False


class TenantOwned(models.Model):
    """
    Abstract base for every row that belongs to exactly one tenant.

    Deliberately not django-tenants: hundreds of small tenants means schema
    migrations across hundreds of schemas, and PayerScore needs cross-tenant
    aggregates that separate schemas make painful (spec section 5.1).
    """

    organization = models.ForeignKey(
        "tenancy.Organization",
        on_delete=models.CASCADE,
        db_index=True,
        related_name="%(app_label)s_%(class)s_set",
    )

    objects = TenantManager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.organization_id is None:
            org_id = get_active_org_id()
            if org_id is None:
                raise NoActiveOrganization(
                    f"{type(self).__name__}.organization is not set and no active "
                    "organization is bound."
                )
            self.organization_id = org_id
        return super().save(*args, **kwargs)
