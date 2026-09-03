from django.contrib import admin

from core.admin import TenantOwnedAdmin
from vault.models import (
    Classification,
    Document,
    DocumentPage,
    Equipment,
    ExtractedField,
    FinancialYear,
    KeyPersonnel,
    Litigation,
    PastProject,
)


@admin.register(Document)
class DocumentAdmin(TenantOwnedAdmin):
    list_display = ("title", "spec", "issued_on", "expires_on", "extracted",
                    "verified_by_human")
    list_filter = ("extracted", "verified_by_human", "spec")
    search_fields = ("title", "reference_number", "content_hash")


@admin.register(Classification)
class ClassificationAdmin(TenantOwnedAdmin):
    list_display = ("category", "financial_class", "valid_from", "valid_to",
                    "verified_against_register_at")
    list_filter = ("scheme", "category")


@admin.register(FinancialYear)
class FinancialYearAdmin(TenantOwnedAdmin):
    list_display = ("year", "turnover", "currency", "audited_accounts")
    list_filter = ("year",)


@admin.register(PastProject)
class PastProjectAdmin(TenantOwnedAdmin):
    list_display = ("name", "client_name", "value", "completed_on", "is_ongoing")
    search_fields = ("name", "client_name")


for model in (DocumentPage, ExtractedField, Litigation, Equipment, KeyPersonnel):
    admin.site.register(model, TenantOwnedAdmin)
