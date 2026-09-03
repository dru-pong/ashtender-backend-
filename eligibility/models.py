"""
Immutable, timestamped eligibility results.

A check is never updated in place.  Re-running produces a new row, because
"why did the system tell me I was ineligible last Tuesday?" is a question a
tenant will ask, and the only defensible answer quotes the tender sentence and
names the document as they stood last Tuesday.
"""
from django.db import models

from core.enums import Verdict
from core.models import TenantOwnedTimeStamped


class EligibilityCheck(TenantOwnedTimeStamped):
    """One evaluation of one tender for one tenant."""

    tender = models.ForeignKey(
        "tenders.Tender", on_delete=models.CASCADE, related_name="eligibility_checks"
    )
    computed_at = models.DateTimeField(db_index=True)
    verdict = models.CharField(max_length=10, choices=Verdict.choices)
    engine_version = models.CharField(
        max_length=32,
        help_text="Bumped whenever rule logic changes, so old verdicts stay "
        "explainable against the rules that produced them.",
    )
    screened_out_reason = models.CharField(max_length=64, blank=True)
    assessed_against_closing_at = models.DateTimeField(null=True, blank=True)
    summary = models.TextField(blank=True)
    inputs_digest = models.CharField(
        max_length=64, blank=True,
        help_text="Hash of the profile, documents and rules used. Changes here "
        "are what trigger a recompute.",
    )

    class Meta:
        ordering = ["-computed_at"]
        indexes = [
            models.Index(fields=["organization", "tender", "-computed_at"]),
            models.Index(fields=["organization", "verdict", "-computed_at"]),
        ]

    def __str__(self):
        return f"{self.tender_id} -> {self.verdict}"

    @property
    def blocking_findings(self):
        return self.findings.filter(verdict__in=[Verdict.FAIL, Verdict.UNKNOWN])


class RequirementFinding(TenantOwnedTimeStamped):
    """
    One reason a check came out the way it did.

    ``source_text`` and ``satisfied_by`` are the pair that makes a verdict
    arguable: the tender sentence that imposed the requirement, and the file
    that answers it.  A finding with neither is a system asserting something
    about a contractor's business without showing its work, and the spec's
    invariant is that it does not ship.
    """

    class Code(models.TextChoices):
        OBJECT_TYPE = "OBJECT_TYPE", "Object type not bid by this firm"
        CLASSIFICATION = "CLASSIFICATION", "Contractor classification"
        CLASSIFICATION_LAPSED = "CLASSIFICATION_LAPSED", "Classification validity lapsed"
        CLASSIFICATION_UNVERIFIED = "CLASSIFICATION_UNVERIFIED", "Classification not register-verified"
        VALUE_CAP = "VALUE_CAP", "Financial class value cap"
        VALUE_CAP_BOUNDARY = "VALUE_CAP_BOUNDARY", "Near financial class cap boundary"
        DEBARMENT = "DEBARMENT", "Debarment"
        LATE = "LATE", "Insufficient preparation time"
        DOCUMENT = "DOCUMENT", "Required document"
        FINANCIAL_YEARS = "FINANCIAL_YEARS", "Five-year financial record"
        SIMILAR_WORKS = "SIMILAR_WORKS", "Similar works experience"
        DOMESTIC_PREFERENCE = "DOMESTIC_PREFERENCE", "Domestic contractor status"
        JV_REVIEW = "JV_REVIEW", "Joint venture requires human review"

    # Named eligibility_check, not check: Model.check() is a Django classmethod
    # and shadowing it breaks system checks (models.E020).
    eligibility_check = models.ForeignKey(
        EligibilityCheck, on_delete=models.CASCADE, related_name="findings"
    )
    code = models.CharField(max_length=32, choices=Code.choices)
    spec = models.ForeignKey(
        "catalog.RequirementSpec", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="findings",
    )
    verdict = models.CharField(max_length=10, choices=Verdict.choices)
    is_mandatory = models.BooleanField(default=True)
    reason = models.TextField(
        help_text="Plain English, addressed to the contractor, not a code."
    )
    source_text = models.TextField(
        blank=True, help_text="The tender sentence this requirement came from."
    )
    satisfied_by = models.ForeignKey(
        "vault.Document", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="findings_satisfied",
    )
    satisfied_by_classification = models.ForeignKey(
        "vault.Classification", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="findings_satisfied",
    )
    confidence = models.FloatField(null=True, blank=True)
    remedy = models.TextField(
        blank=True, help_text="What the tenant can do about it, when anything can be."
    )

    class Meta:
        ordering = ["eligibility_check_id", "code"]
        indexes = [models.Index(fields=["eligibility_check", "verdict"])]

    def __str__(self):
        return f"{self.code}: {self.verdict}"
