from django.contrib import admin

from catalog.models import (
    ClassificationScheme,
    CredentialAuthority,
    Discipline,
    District,
    DomainRequirementRule,
    EntityContact,
    FinancialClassCap,
    FxRate,
    ProcurementDomain,
    ProcurementPlanItem,
    ProcuringEntity,
    Region,
    RequirementSpec,
    WorkCategory,
)


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "capital", "district_seed_complete")
    list_filter = ("district_seed_complete",)


@admin.register(District)
class DistrictAdmin(admin.ModelAdmin):
    list_display = ("name", "region", "kind", "capital")
    list_filter = ("region", "kind")
    search_fields = ("name", "capital")


@admin.register(ClassificationScheme)
class ClassificationSchemeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "register_ingestible", "publication_cadence")


@admin.register(WorkCategory)
class WorkCategoryAdmin(admin.ModelAdmin):
    list_display = ("scheme", "letter", "name", "max_financial_class")
    list_filter = ("scheme",)


@admin.register(FinancialClassCap)
class FinancialClassCapAdmin(admin.ModelAdmin):
    list_display = (
        "scheme", "category", "financial_class", "amount", "currency",
        "effective_from", "is_stale",
    )
    list_filter = ("scheme", "is_stale", "currency")


@admin.register(FxRate)
class FxRateAdmin(admin.ModelAdmin):
    list_display = ("currency_pair", "rate", "as_of", "source")
    list_filter = ("currency_pair", "source")


@admin.register(RequirementSpec)
class RequirementSpecAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "authority", "validity_mode", "recency_months",
                    "is_conditional")
    list_filter = ("validity_mode", "is_conditional", "authority")
    search_fields = ("code", "name")


@admin.register(ProcuringEntity)
class ProcuringEntityAdmin(admin.ModelAdmin):
    list_display = ("name", "channel", "entity_category", "region")
    list_filter = ("channel", "entity_category", "region")
    search_fields = ("name", "ocds_party_id")


@admin.register(EntityContact)
class EntityContactAdmin(admin.ModelAdmin):
    list_display = ("full_name", "title", "entity", "source_tier", "verified_by_human")
    list_filter = ("source_tier", "verified_by_human")
    search_fields = ("full_name", "entity__name")


admin.site.register(
    [ProcurementDomain, Discipline, DomainRequirementRule, CredentialAuthority,
     ProcurementPlanItem]
)
