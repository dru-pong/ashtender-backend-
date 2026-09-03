"""
The document vault and the five-year record it has to hold.

Section IV of the PPA Standard Tender Documents asks for a five-year lookback
on financial standing, turnover, litigation and similar works.  That makes the
five-year window a hard schema requirement, not a reporting nicety: "missing
FY-3 audited accounts" has to be a blocking finding, and it can only be one if
the model can tell a missing year from an unreported one (spec section 3.5).
"""
import hashlib

from django.db import models

from core.enums import ValidityMode
from core.models import TenantOwnedTimeStamped


class Classification(TenantOwnedTimeStamped):
    """
    A contractor classification this tenant holds.

    ``verified_against_register_at`` is the trust anchor.  Where the ministry's
    published register covers the firm, that register -- not OCR of the
    certificate -- decides whether the classification is real.  A certificate
    image can be stale, edited, or simply superseded by a reclassification
    exercise the holder did not re-enter.
    """

    scheme = models.ForeignKey("catalog.ClassificationScheme", on_delete=models.PROTECT)
    category = models.ForeignKey("catalog.WorkCategory", on_delete=models.PROTECT)
    financial_class = models.PositiveSmallIntegerField(help_text="1 is the highest capacity.")
    certificate = models.ForeignKey(
        "vault.Document", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="classifications_evidenced",
    )
    issued_on = models.DateField(null=True, blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    verified_against_register_at = models.DateTimeField(null=True, blank=True)
    register_entry = models.ForeignKey(
        "tenders.ClassificationRegistryEntry", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="matched_classifications",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["scheme__code", "category__letter"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "scheme", "category", "valid_from"],
                name="uniq_classification_per_period",
            )
        ]

    def __str__(self):
        return f"{self.category.letter}{self.financial_class}"

    def is_current_on(self, on_date) -> bool | None:
        """
        True / False / None, never a bare bool.

        None means the validity window is unrecorded, which is a different
        thing from lapsed and must reach a human rather than a FAIL.
        """
        if self.valid_to is None:
            return None
        if self.valid_from and on_date < self.valid_from:
            return False
        return on_date <= self.valid_to


class Document(TenantOwnedTimeStamped):
    """
    One uploaded artefact plus whatever we know about its validity.

    Holding other firms' tax certificates, audited accounts and bank
    references makes AshKop a data controller under Act 843, which is why
    registration with the Data Protection Commission is a launch gate rather
    than a compliance chore.  See docs/DPC.md.
    """

    spec = models.ForeignKey(
        "catalog.RequirementSpec", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="documents",
        help_text="Null until classification (manual or DOC_CLASSIFY) assigns one.",
    )
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to="vault/%Y/%m/")
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    mime_type = models.CharField(max_length=128, blank=True)
    page_count = models.PositiveSmallIntegerField(null=True, blank=True)

    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    issuing_authority = models.ForeignKey(
        "catalog.CredentialAuthority", on_delete=models.SET_NULL, null=True, blank=True
    )
    reference_number = models.CharField(max_length=128, blank=True)

    extracted = models.BooleanField(
        default=False, help_text="A VLM has read this document."
    )
    verified_by_human = models.BooleanField(
        default=False,
        help_text="Someone confirmed the extracted dates against the page crop. "
        "Unverified extraction may support a screening PASS but may never "
        "support READY_TO_SUBMIT.",
    )
    verified_by = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="supersedes"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "spec", "-issued_on"]),
            models.Index(fields=["organization", "expires_on"]),
        ]

    def __str__(self):
        return self.title

    def compute_hash(self) -> str:
        digest = hashlib.sha256()
        self.file.open("rb")
        try:
            for chunk in self.file.chunks():
                digest.update(chunk)
        finally:
            self.file.close()
        return digest.hexdigest()

    def days_until_expiry(self, on_date):
        if self.expires_on is None:
            return None
        return (self.expires_on - on_date).days


class DocumentPage(TenantOwnedTimeStamped):
    """
    A rendered page image, kept so a human can confirm an extracted value
    against the crop the model actually looked at.
    """

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="pages")
    page_number = models.PositiveSmallIntegerField()
    image = models.ImageField(upload_to="vault/pages/%Y/%m/")
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    ocr_text = models.TextField(blank=True)

    class Meta:
        ordering = ["document_id", "page_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "page_number"], name="uniq_page_per_document"
            )
        ]


class ExtractedField(TenantOwnedTimeStamped):
    """
    One value a VLM pulled off a page, with the box it came from.

    The bbox is what makes verification cheap: the UI crops that rectangle and
    asks a human "is this date 2026-11-30?" instead of asking them to re-read
    the whole certificate.  Bboxes are normalised 0-1 so they survive
    re-rendering at a different resolution.
    """

    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="extracted_fields"
    )
    page = models.ForeignKey(
        DocumentPage, on_delete=models.SET_NULL, null=True, blank=True, related_name="fields"
    )
    field_name = models.CharField(max_length=64)
    value_raw = models.TextField(blank=True)
    value_date = models.DateField(null=True, blank=True)
    value_decimal = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    bbox = models.JSONField(
        null=True, blank=True, help_text="[x0, y0, x1, y1] normalised to 0-1."
    )
    confidence = models.FloatField(null=True, blank=True)
    llm_call = models.ForeignKey(
        "llm.LLMCall", on_delete=models.SET_NULL, null=True, blank=True, related_name="fields"
    )
    confirmed_by_human = models.BooleanField(default=False)

    class Meta:
        ordering = ["document_id", "field_name"]
        indexes = [models.Index(fields=["organization", "document", "field_name"])]

    @property
    def bbox_is_usable(self) -> bool:
        """Reject degenerate or out-of-bounds boxes; the UI falls back to the full page."""
        if not isinstance(self.bbox, (list, tuple)) or len(self.bbox) != 4:
            return False
        try:
            x0, y0, x1, y1 = (float(v) for v in self.bbox)
        except (TypeError, ValueError):
            return False
        if not all(0.0 <= v <= 1.0 for v in (x0, y0, x1, y1)):
            return False
        return x1 > x0 and y1 > y0


class FinancialYear(TenantOwnedTimeStamped):
    """One year of the five-year financial record Section IV demands."""

    year = models.PositiveSmallIntegerField()
    period_end = models.DateField(null=True, blank=True)
    turnover = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="GHS")
    profit_before_tax = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    net_worth = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    working_capital = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    audited_accounts = models.ForeignKey(
        Document, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    auditor_name = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ["-year"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "year"], name="uniq_fy_per_org")
        ]

    def __str__(self):
        return str(self.year)

    @property
    def is_complete(self) -> bool:
        return self.turnover is not None and self.audited_accounts_id is not None


class PastProject(TenantOwnedTimeStamped):
    """
    Similar-works experience, with the client contact the forms ask for.

    ``completion_cert`` matters more than the narrative: an experience schedule
    without certificates is an assertion, and evaluators treat it as one.
    """

    name = models.CharField(max_length=255)
    disciplines = models.ManyToManyField(
        "catalog.Discipline", blank=True, related_name="past_projects"
    )
    client_entity = models.ForeignKey(
        "catalog.ProcuringEntity", on_delete=models.SET_NULL, null=True, blank=True
    )
    client_name = models.CharField(max_length=255, blank=True)
    client_contact_name = models.CharField(max_length=160, blank=True)
    client_contact_phone = models.CharField(max_length=64, blank=True)
    client_contact_email = models.EmailField(blank=True)
    district = models.ForeignKey(
        "catalog.District", on_delete=models.SET_NULL, null=True, blank=True
    )
    value = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="GHS")
    started_on = models.DateField(null=True, blank=True)
    completed_on = models.DateField(null=True, blank=True)
    is_ongoing = models.BooleanField(
        default=False, help_text="Section IV asks separately for work under way or committed."
    )
    percent_complete = models.PositiveSmallIntegerField(null=True, blank=True)
    completion_cert = models.ForeignKey(
        Document, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    role = models.CharField(
        max_length=32, blank=True, help_text="PRIME | JV_MEMBER | SUBCONTRACTOR"
    )

    class Meta:
        ordering = ["-completed_on", "-started_on"]

    def __str__(self):
        return self.name


class Litigation(TenantOwnedTimeStamped):
    """Five-year litigation history: parties, disputed amount, award."""

    year = models.PositiveSmallIntegerField()
    counterparty = models.CharField(max_length=255)
    cause = models.TextField(blank=True)
    disputed_amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=3, default="GHS")
    outcome = models.CharField(max_length=255, blank=True)
    award_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    is_resolved = models.BooleanField(default=False)
    evidence = models.ForeignKey(
        Document, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-year"]
        verbose_name_plural = "litigation"


class Equipment(TenantOwnedTimeStamped):
    """Major plant items for the equipment schedule."""

    class Ownership(models.TextChoices):
        OWNED = "OWNED", "Owned"
        LEASED = "LEASED", "Leased"
        TO_BE_HIRED = "TO_BE_HIRED", "To be hired"
        TO_BE_PURCHASED = "TO_BE_PURCHASED", "To be purchased"

    name = models.CharField(max_length=160)
    make_model = models.CharField(max_length=160, blank=True)
    year_of_manufacture = models.PositiveSmallIntegerField(null=True, blank=True)
    quantity = models.PositiveSmallIntegerField(default=1)
    capacity = models.CharField(max_length=64, blank=True)
    ownership = models.CharField(
        max_length=20, choices=Ownership.choices, default=Ownership.OWNED
    )
    registration_number = models.CharField(max_length=64, blank=True)
    condition = models.CharField(max_length=64, blank=True)
    proof = models.ForeignKey(
        Document, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "equipment"

    def __str__(self):
        return f"{self.name} x{self.quantity}"


class KeyPersonnel(TenantOwnedTimeStamped):
    """Site management and technical staff for the personnel schedule."""

    full_name = models.CharField(max_length=160)
    position = models.CharField(max_length=160)
    qualification = models.CharField(max_length=255, blank=True)
    professional_body = models.CharField(
        max_length=64, blank=True, help_text="e.g. GhIE membership, which some TDS clauses require."
    )
    membership_number = models.CharField(max_length=64, blank=True)
    years_experience = models.PositiveSmallIntegerField(null=True, blank=True)
    years_in_similar_works = models.PositiveSmallIntegerField(null=True, blank=True)
    cv = models.ForeignKey(
        Document, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["position", "full_name"]
        verbose_name_plural = "key personnel"

    def __str__(self):
        return f"{self.full_name} - {self.position}"
