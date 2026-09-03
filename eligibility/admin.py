from django.contrib import admin

from core.admin import TenantOwnedAdmin
from eligibility.models import EligibilityCheck, RequirementFinding


class RequirementFindingInline(admin.TabularInline):
    model = RequirementFinding
    extra = 0
    fields = ("code", "spec", "verdict", "reason", "source_text", "satisfied_by", "remedy")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj):
        """Checks are immutable; findings are written by the engine only."""
        return False


@admin.register(EligibilityCheck)
class EligibilityCheckAdmin(TenantOwnedAdmin):
    list_display = ("tender", "verdict", "engine_version", "screened_out_reason",
                    "computed_at")
    list_filter = ("verdict", "engine_version", "screened_out_reason")
    readonly_fields = ("computed_at", "inputs_digest", "engine_version", "verdict",
                       "summary", "screened_out_reason", "assessed_against_closing_at")
    inlines = [RequirementFindingInline]


@admin.register(RequirementFinding)
class RequirementFindingAdmin(TenantOwnedAdmin):
    list_display = ("code", "verdict", "spec", "is_mandatory", "eligibility_check")
    list_filter = ("verdict", "code", "is_mandatory")
    search_fields = ("reason", "source_text")
