from django.contrib import admin

from llm.models import CircuitState, LLMCall, ModelRoute


@admin.register(ModelRoute)
class ModelRouteAdmin(admin.ModelAdmin):
    """
    Routing is data on purpose: retargeting a task at a cheaper model is an
    admin edit, not a deploy.
    """

    list_display = ("task", "model_name", "priority", "is_active", "supports_vision",
                    "temperature", "max_tokens", "cost_per_1k_input", "cost_per_1k_output")
    list_filter = ("task", "is_active", "supports_vision")
    list_editable = ("priority", "is_active", "cost_per_1k_input", "cost_per_1k_output")


@admin.register(LLMCall)
class LLMCallAdmin(admin.ModelAdmin):
    list_display = ("created_at", "task", "model_name", "status", "organization",
                    "tokens_in", "tokens_out", "cost_usd", "latency_ms", "attempt_count")
    list_filter = ("status", "task", "model_name", "organization")
    search_fields = ("content_hash", "error")
    readonly_fields = [field.name for field in LLMCall._meta.fields]
    date_hierarchy = "created_at"


@admin.register(CircuitState)
class CircuitStateAdmin(admin.ModelAdmin):
    list_display = ("model_name", "consecutive_failures", "opened_at", "last_failure_at")
    actions = ["close_circuit"]

    @admin.action(description="Close circuit (reset failure count)")
    def close_circuit(self, request, queryset):
        queryset.update(consecutive_failures=0, opened_at=None, last_error="")
