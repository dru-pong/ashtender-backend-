from django.contrib import admin

from tenders.models import (
    AwardHistory,
    ClassificationRegistryEntry,
    DebarmentEntry,
    Source,
    Tender,
    TenderClassRequirement,
    TenderDiscipline,
    TenderRequirement,
    TenderSighting,
)


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("name", "channel", "strategy", "is_active",
                    "robots_allows_automation", "last_harvested_at",
                    "first_sighting_wins_count")
    list_filter = ("channel", "strategy", "is_active", "robots_allows_automation")


@admin.register(Tender)
class TenderAdmin(admin.ModelAdmin):
    list_display = ("reference_number", "entity", "object_type", "estimated_value",
                    "currency", "closing_at", "is_cancelled")
    list_filter = ("object_type", "method", "funding_source", "is_cancelled", "entity")
    search_fields = ("reference_number", "ocid", "title")
    date_hierarchy = "closing_at"


@admin.register(ClassificationRegistryEntry)
class ClassificationRegistryEntryAdmin(admin.ModelAdmin):
    list_display = ("company_name", "scheme", "notation", "list_seq_no",
                    "valid_from", "valid_to", "status_as_published", "list_as_of")
    list_filter = ("scheme", "status_as_published", "list_as_of")
    search_fields = ("company_name", "company_name_normalised", "notation")


@admin.register(DebarmentEntry)
class DebarmentEntryAdmin(admin.ModelAdmin):
    list_display = ("company_name", "authority", "from_date", "to_date", "country")
    list_filter = ("authority",)
    search_fields = ("company_name", "company_name_normalised")


@admin.register(AwardHistory)
class AwardHistoryAdmin(admin.ModelAdmin):
    list_display = ("winner_name", "entity", "value", "currency", "award_date",
                    "bidder_count")
    list_filter = ("entity",)
    search_fields = ("winner_name", "winner_name_normalised", "ocid")


admin.site.register([TenderSighting, TenderRequirement, TenderDiscipline,
                     TenderClassRequirement])
