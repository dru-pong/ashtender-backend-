"""
Admin bases.

Django admin is the second of the two places allowed to cross tenants, and it
says so out loud: every changelist goes through ``across_tenants()``, which
logs a warning naming the model.  Staff browsing a tenant's documents is a
legitimate support action and an auditable one.
"""
from django.contrib import admin

from core.tenancy import TenantOwned


class TenantOwnedAdmin(admin.ModelAdmin):
    """
    Base for any TenantOwned model exposed in admin.

    ``organization`` is forced into list_display and list_filter so a staff
    member reading a changelist can always see whose row they are looking at.
    Losing track of that is how support becomes a data incident.
    """

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        model = queryset.model
        if issubclass(model, TenantOwned):
            return queryset.across_tenants(
                reason=f"django-admin changelist by {request.user} on {model.__name__}"
            )
        return queryset

    def get_list_display(self, request):
        display = list(super().get_list_display(request))
        if "organization" not in display:
            display.insert(0, "organization")
        return display

    def get_list_filter(self, request):
        filters = list(super().get_list_filter(request))
        if "organization" not in filters:
            filters.insert(0, "organization")
        return filters
