"""
Receivables: the wedge, and the moat.

Ships before the eligibility engine on purpose.  It needs no ingestion
pipeline, it addresses a pain contractors already feel daily -- being owed
money and unable to evidence the claim -- and it is the reason a tenant uploads
the documents the eligibility engine later needs.

Two dates carry almost all the value: submitted -> certified, and certified ->
paid.  Aggregated across tenants they become PayerScore, which cannot be
scraped or bought because it does not exist anywhere else.

Boundary, kept deliberately sharp: AshTender tracks and documents claims.  It
does not chase payment.  Blur that and AshKop gets blamed for Treasury cash flow.
"""
from datetime import date
from decimal import Decimal

from django.db import models

from core.enums import ClaimOutcome, FundingSource, IPCStatus
from core.models import TenantOwnedTimeStamped


class Contract(TenantOwnedTimeStamped):
    """Work actually won.  The root of the receivables tree."""

    entity = models.ForeignKey(
        "catalog.ProcuringEntity", on_delete=models.PROTECT, related_name="contracts"
    )
    tender = models.ForeignKey(
        "tenders.Tender", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="contracts",
    )
    name = models.CharField(max_length=255)
    contract_number = models.CharField(max_length=128, blank=True)
    ppa_reference = models.CharField(max_length=128, blank=True)
    funding_source = models.CharField(
        max_length=20, choices=FundingSource.choices,
        help_text="The single most predictive field for how long payment takes. "
        "Rarely stated in any document; usually established by asking.",
    )
    value = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, default="GHS")
    award_date = models.DateField(null=True, blank=True)
    commencement_date = models.DateField(null=True, blank=True)
    completion_date = models.DateField(null=True, blank=True)
    duration_months = models.PositiveSmallIntegerField(null=True, blank=True)
    retention_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    defects_liability_months = models.PositiveSmallIntegerField(null=True, blank=True)
    district = models.ForeignKey(
        "catalog.District", on_delete=models.SET_NULL, null=True, blank=True
    )
    is_complete = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-award_date"]
        indexes = [models.Index(fields=["organization", "funding_source"])]

    def __str__(self):
        return self.name

    @property
    def total_certified(self) -> Decimal:
        return self.ipcs.for_org(self.organization_id).aggregate(
            total=models.Sum("amount_certified")
        )["total"] or Decimal("0")

    @property
    def total_paid(self) -> Decimal:
        return self.ipcs.for_org(self.organization_id).aggregate(
            total=models.Sum("amount_paid")
        )["total"] or Decimal("0")

    @property
    def outstanding(self) -> Decimal:
        return self.total_certified - self.total_paid


class IPC(TenantOwnedTimeStamped):
    """
    Interim Payment Certificate.

    Every date is separate and every one is nullable, because the whole point
    is to know *which* step a claim is stuck on.  A single "status" field would
    lose the aging that makes this useful: a certificate stuck 300 days at
    CERTIFIED is a Treasury problem, and one stuck 300 days at SUBMITTED is an
    engineer's desk problem, and the contractor does completely different
    things about each.
    """

    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="ipcs")
    number = models.CharField(max_length=32)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    amount_claimed = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    amount_certified = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    amount_paid = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="GHS")
    retention_held = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )

    prepared_on = models.DateField(null=True, blank=True)
    submitted_on = models.DateField(null=True, blank=True)
    certified_on = models.DateField(null=True, blank=True)
    validated_on = models.DateField(
        null=True, blank=True,
        help_text="Date an arrears-validation exercise accepted the claim.",
    )
    paid_on = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=20, choices=IPCStatus.choices, default=IPCStatus.PREPARED
    )
    rejection_reason = models.TextField(blank=True)
    evidence = models.ManyToManyField("vault.Document", blank=True, related_name="ipcs")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["contract_id", "number"]
        verbose_name = "IPC"
        verbose_name_plural = "IPCs"
        constraints = [
            models.UniqueConstraint(
                fields=["contract", "number"], name="uniq_ipc_number_per_contract"
            )
        ]
        indexes = [models.Index(fields=["organization", "status", "submitted_on"])]

    def __str__(self):
        return f"IPC {self.number} ({self.contract.name})"

    @property
    def days_to_certify(self) -> int | None:
        if self.submitted_on and self.certified_on:
            return (self.certified_on - self.submitted_on).days
        return None

    @property
    def days_to_pay(self) -> int | None:
        if self.certified_on and self.paid_on:
            return (self.paid_on - self.certified_on).days
        return None

    def days_outstanding(self, as_of: date | None = None) -> int | None:
        """How long this claim has been sitting, wherever it is sitting."""
        as_of = as_of or date.today()
        if self.paid_on:
            return None
        anchor = self.certified_on or self.submitted_on
        return (as_of - anchor).days if anchor else None

    @property
    def amount_outstanding(self) -> Decimal:
        certified = self.amount_certified or Decimal("0")
        paid = self.amount_paid or Decimal("0")
        return certified - paid


class ArrearsClaim(TenantOwnedTimeStamped):
    """
    A bundle of IPCs submitted into a government arrears-validation exercise.

    Modelled separately from the IPCs because validation outcomes attach to the
    submission, not the certificate: the 2025 exercise validated some claims,
    reduced others and rejected around GH¢12bn outright, and a contractor needs
    to know which of their certificates went into which bundle and what came back.
    """

    reference = models.CharField(max_length=128, blank=True)
    exercise_name = models.CharField(
        max_length=255, blank=True,
        help_text="e.g. Auditor-General validation of claims as at 31 Dec 2024.",
    )
    ipcs = models.ManyToManyField(IPC, blank=True, related_name="arrears_claims")
    submitted_on = models.DateField(null=True, blank=True)
    amount_submitted = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    amount_validated = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    outcome = models.CharField(
        max_length=20, choices=ClaimOutcome.choices, default=ClaimOutcome.PENDING
    )
    outcome_on = models.DateField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    evidence = models.ManyToManyField(
        "vault.Document", blank=True, related_name="arrears_claims"
    )

    class Meta:
        ordering = ["-submitted_on"]

    def __str__(self):
        return self.reference or f"Claim {self.pk}"


class PayerScore(models.Model):
    """
    How long a given buyer, on a given funding source, actually takes to pay.

    Global and derived, not tenant-owned: it is computed by aggregating every
    tenant's IPC dates.  That is the one legitimate cross-tenant read in the
    system, and it is fenced two ways -- only aggregates are ever stored here,
    and nothing is published until the minimum-n floor is met, because a median
    over two contracts from one firm is that firm's payment history wearing a
    disguise.

    Turning funding source from a label into a number is what lets a contractor
    see that a GH¢2m Road Fund job paying in 400 days is worse than a GH¢800k
    donor job paying in 60.
    """

    entity = models.ForeignKey(
        "catalog.ProcuringEntity", on_delete=models.CASCADE, related_name="payer_scores"
    )
    funding_source = models.CharField(max_length=20, choices=FundingSource.choices)
    median_days_to_certify = models.PositiveIntegerField(null=True, blank=True)
    median_days_to_pay = models.PositiveIntegerField(null=True, blank=True)
    p90_days_to_pay = models.PositiveIntegerField(null=True, blank=True)
    rejection_rate = models.FloatField(null=True, blank=True)
    contract_count = models.PositiveIntegerField(default=0)
    ipc_count = models.PositiveIntegerField(default=0)
    distinct_tenant_count = models.PositiveIntegerField(default=0)
    is_publishable = models.BooleanField(
        default=False,
        help_text="False until both the contract and distinct-tenant floors are "
        "met. Unpublishable rows are computed but never served.",
    )
    computed_at = models.DateTimeField()

    class Meta:
        ordering = ["entity__name", "funding_source"]
        constraints = [
            models.UniqueConstraint(
                fields=["entity", "funding_source"], name="uniq_payer_score"
            )
        ]

    def __str__(self):
        return f"{self.entity.name} / {self.funding_source}"
