from django.contrib import admin

from bids.models import ActionRequest, Bid, BidTeam, Interaction
from core.admin import TenantOwnedAdmin


class BidTeamInline(admin.TabularInline):
    model = BidTeam
    extra = 0


@admin.register(Bid)
class BidAdmin(TenantOwnedAdmin):
    list_display = ("tender", "state", "tender_price", "currency",
                    "is_joint_venture", "submitted_at")
    list_filter = ("state", "is_joint_venture", "submission_mode")
    inlines = [BidTeamInline]


@admin.register(Interaction)
class InteractionAdmin(TenantOwnedAdmin):
    list_display = ("entity", "kind", "occurred_at", "contact",
                    "funding_source_learned", "next_action")
    list_filter = ("kind", "entity")
    search_fields = ("notes", "outcome", "participants")


@admin.register(ActionRequest)
class ActionRequestAdmin(TenantOwnedAdmin):
    list_display = ("title", "kind", "status", "priority", "due_at", "assigned_to")
    list_filter = ("kind", "status")
    search_fields = ("title", "brief")


admin.site.register(BidTeam, TenantOwnedAdmin)
