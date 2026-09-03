"""
Routing table and call log for every model invocation.

Two rules make this app worth existing as an app:

*   Configuration is data.  Swapping a model for a task is an admin edit, not
    a deploy, because the cheap-model-for-cheap-task tuning that keeps a
    low-RAM VPS affordable happens continuously.
*   Every call is logged against a tenant with token counts.  Per-tenant cost
    attribution has to exist *before* pricing is set, or the success-fee model
    gets priced on a guess.
"""
from django.db import models

from core.enums import LLMTask
from core.models import TimeStampedModel


class ModelRoute(TimeStampedModel):
    """Which model, at what settings, serves a given task."""

    task = models.CharField(max_length=32, choices=LLMTask.choices)
    model_name = models.CharField(max_length=128)
    temperature = models.FloatField(default=0.0)
    max_tokens = models.PositiveIntegerField(default=2048)
    json_schema = models.JSONField(
        default=dict, blank=True,
        help_text="Response schema. A response that does not validate gets one "
        "repair attempt, then hard-fails to UNKNOWN rather than being guessed at.",
    )
    system_prompt = models.TextField(blank=True)
    supports_vision = models.BooleanField(default=False)
    cost_per_1k_input = models.DecimalField(
        max_digits=10, decimal_places=6, default=0, help_text="USD."
    )
    cost_per_1k_output = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    is_active = models.BooleanField(default=True)
    priority = models.SmallIntegerField(
        default=0, help_text="Higher wins when several routes are active for a task."
    )

    class Meta:
        ordering = ["task", "-priority"]
        indexes = [models.Index(fields=["task", "is_active", "-priority"])]

    def __str__(self):
        return f"{self.task} -> {self.model_name}"


class LLMCall(TimeStampedModel):
    """
    One request, logged whether it succeeded or not.

    ``organization`` is nullable rather than TenantOwned because catalogue-wide
    work (mining the OCDS title corpus, parsing a ministry register) belongs to
    no tenant, and forcing a fake organization onto those rows would corrupt
    exactly the cost attribution this table exists for.
    """

    class Status(models.TextChoices):
        OK = "OK", "OK"
        CACHED = "CACHED", "Served from cache"
        SCHEMA_REPAIRED = "SCHEMA_REPAIRED", "Repaired after invalid JSON"
        SCHEMA_FAILED = "SCHEMA_FAILED", "Invalid JSON after repair"
        PROVIDER_ERROR = "PROVIDER_ERROR", "Provider error"
        CIRCUIT_OPEN = "CIRCUIT_OPEN", "Skipped, circuit open"
        TIMEOUT = "TIMEOUT", "Timed out"

    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="llm_calls", db_index=True,
    )
    task = models.CharField(max_length=32, choices=LLMTask.choices, db_index=True)
    route = models.ForeignKey(
        ModelRoute, on_delete=models.SET_NULL, null=True, blank=True, related_name="calls"
    )
    model_name = models.CharField(max_length=128)
    status = models.CharField(max_length=20, choices=Status.choices)
    content_hash = models.CharField(
        max_length=64, db_index=True,
        help_text="sha256 over task + model + prompt payload. Re-extracting an "
        "unchanged document costs nothing.",
    )
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=1)
    error = models.TextField(blank=True)
    response = models.JSONField(default=dict, blank=True)
    prompt_preview = models.TextField(
        blank=True,
        help_text="Truncated. Full prompts can contain another firm's audited "
        "accounts, so they are not retained here.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "task", "-created_at"]),
            models.Index(fields=["content_hash", "task"]),
        ]

    def __str__(self):
        return f"{self.task} {self.model_name} {self.status}"


class CircuitState(models.Model):
    """
    Per-model breaker.

    A provider outage must degrade the product -- verdicts fall back to
    UNKNOWN, documents queue for later extraction -- not return 500s to a
    contractor with four days left on a tender window.
    """

    model_name = models.CharField(max_length=128, unique=True)
    consecutive_failures = models.PositiveSmallIntegerField(default=0)
    opened_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    def __str__(self):
        return f"{self.model_name} failures={self.consecutive_failures}"
