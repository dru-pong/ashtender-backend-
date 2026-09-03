from django.db import models

from core.tenancy import (  # noqa: F401  (re-exported for app models)
    ALL_TENANTS,
    NoActiveOrganization,
    TenantManager,
    TenantOwned,
    TenantQuerySet,
    TenantScopeMissing,
    active_org,
)


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantOwnedTimeStamped(TenantOwned, TimeStampedModel):
    class Meta:
        abstract = True
