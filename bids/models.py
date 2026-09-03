"""
Bids, joint ventures, the relationship record, and the work queue.

``Interaction`` is the most sensitive table in the system.  It is a tenant's
accumulated relationship capital -- who they called, what was said, what was
promised -- and it is never shared, aggregated, or surfaced to another tenant
in any form.  PayerScore aggregates IPC dates; it does not touch this table.
"""
from decimal import Decimal

from django.db import models

from core.enums import BidState, InteractionKind, SubmissionMode
from core.models import TenantOwnedTimeStamped


class Bid(TenantOwnedTimeStamped):
    """
    A tenant's engagement with one tender, from discovery to outcome.

    ``foreign_subcontract_pct`` is limb (c) of the Act 914 domestic-contractor
    test: subcontracting more than 50% of total Works value to foreign
    contractors forfeits domestic status.  It lives here rather than on the
    profile because it is a per-bid decision.
    """

    tender = models.ForeignKey("tenders.Tender", on_delete=models.CASCADE, related_name="bids")
    state = models.CharField(
        max_length=24, choices=BidState.choices, default=BidState.DISCOVERED, db_index=True
    )
    latest_check = models.ForeignKey(
        "eligibility.EligibilityCheck", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="bids",
    )
    go_decision_by = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    go_decision_at = models.DateTimeField(null=True, blank=True)
    go_decision_note = models.TextField(blank=True)

    documents_purchased_on = models.DateField(null=True, blank=True)
    document_fee_paid = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    submission_mode = models.CharField(
        max_length=20, choices=SubmissionMode.choices, blank=True
    )
    submission_evidence = models.ForeignKey(
        "vault.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Screenshot or receipt. AshTender assembles and stops; a person submits.",
    )

    tender_price = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="GHS")
    priced_by = models.CharField(
        max_length=160, blank=True,
        help_text="Who priced the Bill of Quantities. Never the system: pricing "
        "the BoQ is the bid, and it is the contractor's judgement.",
    )
    foreign_subcontract_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    is_joint_venture = models.BooleanField(default=False)
    outcome_note = models.TextField(blank=True)
    won_contract = models.ForeignKey(
        "receivables.Contract", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="winning_bid",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "tender"], name="uniq_bid_per_org_tender"
            )
        ]
        indexes = [models.Index(fields=["organization", "state"])]

    def __str__(self):
        return f"{self.tender_id} [{self.state}]"

    @property
    def keeps_domestic_status(self) -> bool | None:
        """Limb (c) only.  None when the subcontracting split is unrecorded."""
        if self.foreign_subcontract_pct is None:
            return None
        return self.foreign_subcontract_pct <= Decimal("50")


class BidTeam(TenantOwnedTimeStamped):
    """
    A joint-venture member and the classification they bring.

    Combining JV members' classifications is done by the engine and then
    flagged for human review rather than reported as a clean verdict, because
    the combination rules vary by scheme and by notice, and getting a JV
    eligibility wrong is expensive in a way a screening miss is not.
    """

    bid = models.ForeignKey(Bid, on_delete=models.CASCADE, related_name="team_members")
    partner_name = models.CharField(max_length=255)
    partner_organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="jv_participations",
        help_text="Set only if the partner is also an AshTender tenant. Even "
        "then, no data crosses: the partner's vault is not readable here.",
    )
    is_lead = models.BooleanField(
        default=False, help_text="ITT 5.4 requires one member in charge."
    )
    share_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    classification_notation = models.CharField(
        max_length=32, blank=True, help_text="As declared by the partner, e.g. K2."
    )
    jv_agreement = models.ForeignKey(
        "vault.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["bid_id", "-is_lead"]

    def __str__(self):
        return self.partner_name


class Interaction(TenantOwnedTimeStamped):
    """
    A record of contact with a buyer.

    Never shared with another tenant, in any form, aggregated or otherwise.

    Notes are kept factual by design.  Offering anything of value to a
    procurement official is an offence carrying debarment of one to six years,
    and a donor fraud finding means World Bank or AfDB blacklisting, which ends
    a company. So this table is built to be readable by an auditor without
    embarrassment -- that is a design requirement, not a disclaimer.
    """

    entity = models.ForeignKey(
        "catalog.ProcuringEntity", on_delete=models.CASCADE, related_name="interactions"
    )
    contact = models.ForeignKey(
        "catalog.EntityContact", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="interactions",
    )
    tender = models.ForeignKey(
        "tenders.Tender", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="interactions",
    )
    kind = models.CharField(max_length=20, choices=InteractionKind.choices)
    occurred_at = models.DateTimeField()
    participants = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    outcome = models.TextField(blank=True)
    funding_source_learned = models.CharField(
        max_length=20, blank=True,
        help_text="No document states the funding source. A five-minute call "
        "does, and it is the highest-value question on the brief.",
    )
    payment_record_note = models.TextField(blank=True)
    next_action = models.CharField(max_length=255, blank=True)
    next_action_at = models.DateTimeField(null=True, blank=True)
    logged_by = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["organization", "entity", "-occurred_at"])]

    def __str__(self):
        return f"{self.kind} {self.entity.name} {self.occurred_at:%Y-%m-%d}"


class ActionRequest(TenantOwnedTimeStamped):
    """
    Something a human needs to do, with everything they need to do it.

    The deliverable is the brief, not the phone number: entity, officer,
    closing date, pre-bid and site-visit dates, the entity's recent awards and
    likely incumbent, every clause the extractor flagged UNKNOWN, and the prior
    interaction history.
    """

    class Kind(models.TextChoices):
        CONFIRM_DOCUMENT = "CONFIRM_DOCUMENT", "Confirm extracted document dates"
        RENEW_DOCUMENT = "RENEW_DOCUMENT", "Renew an expiring document"
        VERIFY_CLASSIFICATION = "VERIFY_CLASSIFICATION", "Verify classification with ministry"
        CALL_ENTITY = "CALL_ENTITY", "Call the procuring entity"
        REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION", "Request formal clarification"
        GO_NOGO = "GO_NOGO", "Go / no-go decision"
        RESOLVE_UNKNOWN = "RESOLVE_UNKNOWN", "Resolve an UNKNOWN finding"
        EXPRESS_INTEREST = "EXPRESS_INTEREST", "Express interest / request documents"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        DONE = "DONE", "Done"
        DISMISSED = "DISMISSED", "Dismissed"

    kind = models.CharField(max_length=24, choices=Kind.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    priority = models.SmallIntegerField(default=0)
    title = models.CharField(max_length=255)
    brief = models.TextField(
        blank=True, help_text="Rendered call brief or checklist. The actual deliverable."
    )
    due_at = models.DateTimeField(null=True, blank=True)
    tender = models.ForeignKey(
        "tenders.Tender", on_delete=models.CASCADE, null=True, blank=True,
        related_name="action_requests",
    )
    bid = models.ForeignKey(
        Bid, on_delete=models.CASCADE, null=True, blank=True, related_name="action_requests"
    )
    finding = models.ForeignKey(
        "eligibility.RequirementFinding", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="action_requests",
    )
    document = models.ForeignKey(
        "vault.Document", on_delete=models.CASCADE, null=True, blank=True,
        related_name="action_requests",
    )
    assigned_to = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-priority", "due_at"]
        indexes = [models.Index(fields=["organization", "status", "-priority"])]

    def __str__(self):
        return self.title
