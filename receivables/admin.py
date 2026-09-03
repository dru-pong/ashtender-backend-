from django.contrib import admin

from core.admin import TenantOwnedAdmin
from receivables.models import IPC, ArrearsClaim, Contract, PayerScore


class IPCInline(admin.TabularInline):
    model = IPC
    extra = 0
    fields = ("number", "amount_claimed", "amount_certified", "amount_paid",
              "submitted_on", "certified_on", "paid_on", "status")


@admin.register(Contract)
class ContractAdmin(TenantOwnedAdmin):
    list_display = ("name", "entity", "funding_source", "value", "currency",
                    "award_date", "is_complete")
    list_filter = ("funding_source", "is_complete", "entity")
    search_fields = ("name", "contract_number", "ppa_reference")
    inlines = [IPCInline]


@admin.register(IPC)
class IPCAdmin(TenantOwnedAdmin):
    list_display = ("number", "contract", "amount_certified", "amount_paid",
                    "submitted_on", "certified_on", "paid_on", "status")
    list_filter = ("status",)


@admin.register(ArrearsClaim)
class ArrearsClaimAdmin(TenantOwnedAdmin):
    list_display = ("reference", "exercise_name", "amount_submitted",
                    "amount_validated", "outcome", "outcome_on")
    list_filter = ("outcome",)


@admin.register(PayerScore)
class PayerScoreAdmin(admin.ModelAdmin):
    """Not TenantOwnedAdmin: PayerScore is a global aggregate with no tenant."""

    list_display = ("entity", "funding_source", "median_days_to_certify",
                    "median_days_to_pay", "p90_days_to_pay", "contract_count",
                    "distinct_tenant_count", "is_publishable")
    list_filter = ("is_publishable", "funding_source")
