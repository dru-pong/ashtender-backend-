"""
Tenders, the channels they arrive on, and the public oracles we check firms
against.  All global -- a tender belongs to nobody.
"""
from django.db import models

from core.enums import (
    Channel,
    ContractForm,
    FundingSource,
    HarvestStrategy,
    Method,
    ObjectType,
    RequirementOrigin,
    RequirementTier,
    SubmissionMode,
)
from core.models import TimeStampedModel


class Source(TimeStampedModel):
    """
    Where tenders come from.

    One model, a handful of strategy classes, and the per-source specifics in
    JSON.  Adding a source is a database row plus selector config, never a new
    Python module -- otherwise the long tail of MMDA websites becomes a
    maintenance job nobody does.
    """

    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=160, unique=True)
    channel = models.CharField(max_length=20, choices=Channel.choices)
    strategy = models.CharField(max_length=20, choices=HarvestStrategy.choices)
    base_url = models.URLField(blank=True)
    config = models.JSONField(
        default=dict, blank=True,
        help_text="Strategy-specific: selectors for LISTING, IMAP folder and "
        "sender patterns for MAILBOX, feed URL for FEED.",
    )
    is_active = models.BooleanField(default=True)
    poll_interval_minutes = models.PositiveIntegerField(default=60)
    last_harvested_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    robots_allows_automation = models.BooleanField(
        default=True,
        help_text="False for sources whose robots.txt or terms disallow "
        "automated access. Those are source-discovery maps only: find the "
        "notice there, then fetch the authoritative copy from the issuer.",
    )
    first_sighting_wins_count = models.PositiveIntegerField(
        default=0,
        help_text="How often this source saw a tender first. Measures which "
        "channels are actually fastest instead of assuming.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Tender(TimeStampedModel):
    """
    A single opportunity, deduplicated across every channel that carried it.

    Dedup key is (issuing entity, reference number); where a reference is
    absent, a content hash over normalised title + closing date + entity.
    """

    entity = models.ForeignKey(
        "catalog.ProcuringEntity", on_delete=models.PROTECT, related_name="tenders"
    )
    reference_number = models.CharField(max_length=160, blank=True, db_index=True)
    ocid = models.CharField(
        max_length=128, blank=True, db_index=True, help_text="OCDS ocid, prefix ocds-uhveoc."
    )
    title = models.TextField()
    description = models.TextField(blank=True)
    dedup_hash = models.CharField(max_length=64, db_index=True, blank=True)

    object_type = models.CharField(max_length=20, choices=ObjectType.choices)
    method = models.CharField(max_length=20, choices=Method.choices, blank=True)
    contract_form = models.CharField(max_length=24, choices=ContractForm.choices, blank=True)
    funding_source = models.CharField(max_length=20, choices=FundingSource.choices, blank=True)
    submission_mode = models.CharField(max_length=20, choices=SubmissionMode.choices, blank=True)

    domain = models.ForeignKey(
        "catalog.ProcurementDomain", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tenders",
    )
    region = models.ForeignKey(
        "catalog.Region", on_delete=models.SET_NULL, null=True, blank=True
    )
    district = models.ForeignKey(
        "catalog.District", on_delete=models.SET_NULL, null=True, blank=True
    )

    estimated_value = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=3, default="GHS")
    value_is_estimated = models.BooleanField(
        default=False, help_text="True when value came from title parsing, not the notice."
    )
    document_fee = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Non-refundable, and the STD leaves the amount blank for the "
        "entity to insert per tender. Never inferred from a range.",
    )

    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    closing_at = models.DateTimeField(null=True, blank=True, db_index=True)
    prebid_meeting_at = models.DateTimeField(null=True, blank=True)
    site_visit_at = models.DateTimeField(null=True, blank=True)
    opening_at = models.DateTimeField(null=True, blank=True)

    std_variant = models.CharField(
        max_length=64, blank=True,
        help_text="Standard Tender Document in use, as declared or extracted. "
        "Never derived from value: Medium Works covers 200k-15M and Major "
        "Works starts at 5M, so 5M-15M is genuinely ambiguous.",
    )
    requires_tender_security = models.BooleanField(null=True, blank=True)
    tender_security_note = models.CharField(max_length=255, blank=True)

    raw_text = models.TextField(blank=True)
    source_url = models.URLField(blank=True, max_length=500)
    is_cancelled = models.BooleanField(default=False)

    class Meta:
        ordering = ["-published_at"]
        indexes = [
            models.Index(fields=["closing_at", "object_type"]),
            models.Index(fields=["entity", "reference_number"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["entity", "reference_number"],
                condition=models.Q(reference_number__gt=""),
                name="uniq_tender_per_entity_reference",
            )
        ]

    def __str__(self):
        return f"{self.reference_number or self.ocid or self.pk}: {self.title[:60]}"


class TenderSighting(TimeStampedModel):
    """
    One channel's observation of a tender.

    Many sightings per tender.  Recording every one is how the system learns
    which channel actually delivers first, rather than which one somebody
    assumed would.
    """

    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="sightings")
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="sightings")
    first_seen_at = models.DateTimeField(db_index=True)
    url = models.URLField(blank=True, max_length=500)
    raw_payload = models.JSONField(default=dict, blank=True)
    is_first_sighting = models.BooleanField(default=False)

    class Meta:
        ordering = ["first_seen_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tender", "source"], name="uniq_sighting_per_tender_source"
            )
        ]


class TenderRequirement(TimeStampedModel):
    """
    A requirement attached to a specific tender.

    ``source_text`` is not optional in spirit even though the column allows
    blank: a finding that cannot quote the sentence it came from cannot be
    defended to the tenant, and the invariant in spec section 6 is that such a
    finding does not ship.
    """

    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="requirements")
    spec = models.ForeignKey(
        "catalog.RequirementSpec", on_delete=models.PROTECT, related_name="tender_requirements"
    )
    origin = models.CharField(max_length=20, choices=RequirementOrigin.choices)
    tier = models.CharField(
        max_length=20, choices=RequirementTier.choices, default=RequirementTier.TENDER_EXPLICIT
    )
    is_mandatory = models.BooleanField(default=True)
    source_text = models.TextField(blank=True)
    extracted_by = models.ForeignKey(
        "llm.LLMCall", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    confidence = models.FloatField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tender", "spec"], name="uniq_requirement_per_tender_spec"
            )
        ]


class TenderDiscipline(TimeStampedModel):
    tender = models.ForeignKey(
        Tender, on_delete=models.CASCADE, related_name="tender_disciplines"
    )
    discipline = models.ForeignKey("catalog.Discipline", on_delete=models.CASCADE)
    confidence = models.FloatField(null=True, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    unit = models.CharField(
        max_length=16, blank=True, help_text="No. | Unit | km | m3 | m2, from TITLE_PARSE."
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tender", "discipline"], name="uniq_discipline_per_tender"
            )
        ]


class TenderClassRequirement(TimeStampedModel):
    """Required contractor classification, e.g. D3K3 or A1B1."""

    tender = models.ForeignKey(
        Tender, on_delete=models.CASCADE, related_name="required_classes"
    )
    scheme = models.ForeignKey("catalog.ClassificationScheme", on_delete=models.PROTECT)
    category = models.ForeignKey("catalog.WorkCategory", on_delete=models.PROTECT)
    min_financial_class = models.PositiveSmallIntegerField()
    or_better = models.BooleanField(
        default=True,
        help_text="True unless the notice pins the class exactly. Class 1 is "
        "the highest capacity, so 'or better' means a numerically lower class.",
    )
    source_text = models.TextField(blank=True)
    origin = models.CharField(
        max_length=20, choices=RequirementOrigin.choices, default=RequirementOrigin.EXPLICIT
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tender", "scheme", "category"], name="uniq_class_req_per_tender"
            )
        ]

    def __str__(self):
        return f"{self.category.letter}{self.min_financial_class}"


class AwardHistory(TimeStampedModel):
    """
    Who has won what from whom.  Mined from OCDS, so the competitor graph and
    the incumbency signal exist on day one instead of after a year of use.
    """

    entity = models.ForeignKey(
        "catalog.ProcuringEntity", on_delete=models.CASCADE, related_name="awards"
    )
    tender = models.ForeignKey(
        Tender, on_delete=models.SET_NULL, null=True, blank=True, related_name="awards"
    )
    ocid = models.CharField(max_length=128, blank=True, db_index=True)
    winner_name = models.CharField(max_length=255)
    winner_name_normalised = models.CharField(max_length=255, blank=True, db_index=True)
    value = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="GHS")
    bidder_count = models.PositiveSmallIntegerField(null=True, blank=True)
    award_date = models.DateField(null=True, blank=True)
    title = models.TextField(blank=True)

    class Meta:
        ordering = ["-award_date"]
        verbose_name_plural = "award history"
        indexes = [models.Index(fields=["entity", "-award_date"])]


class ClassificationRegistryEntry(TimeStampedModel):
    """
    A row from a ministry's published contractor list -- the oracle that
    verifies a tenant's classification without trusting OCR of a certificate.

    The published columns are (seq no, name, classification, postal address,
    valid from, valid to, status), so all of them are stored.  Dropping the
    validity window would make a *lapse* invisible, and the lapse is the whole
    point: a firm can appear on a 2021 good-standing list, be absent from a
    2023 reclassification list, and still be quoting the 2021 certificate.

    Names on these lists do not match tenant-entered legal names exactly, so
    matching is fuzzy (pg_trgm on Postgres) and a match is a suggestion for a
    human, never an automatic identity assertion.
    """

    class Status(models.TextChoices):
        VALID = "VALID", "Valid"
        EXPIRED = "EXPIRED", "Expired"
        SUSPENDED = "SUSPENDED", "Suspended"
        UNKNOWN = "UNKNOWN", "Unknown"

    scheme = models.ForeignKey(
        "catalog.ClassificationScheme", on_delete=models.CASCADE, related_name="registry_entries"
    )
    company_name = models.CharField(max_length=255, db_index=True)
    company_name_normalised = models.CharField(max_length=255, blank=True, db_index=True)
    notation = models.CharField(max_length=32, blank=True, help_text="As printed, e.g. A1B1.")
    categories = models.JSONField(
        default=list, blank=True, help_text='[{"letter": "A", "class": 1}, ...]'
    )
    postal_address = models.CharField(max_length=255, blank=True)
    list_seq_no = models.PositiveIntegerField(null=True, blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    status_as_published = models.CharField(
        max_length=20, choices=Status.choices, default=Status.UNKNOWN
    )
    list_as_of = models.DateField(
        help_text="The 'as at' date printed on the list, not the ingest date."
    )
    list_url = models.URLField(max_length=500, blank=True)
    list_effective_from = models.DateField(
        null=True, blank=True,
        help_text="For reclassification notices that take effect on a stated date.",
    )
    extracted_by = models.ForeignKey(
        "llm.LLMCall", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-list_as_of", "company_name"]
        verbose_name_plural = "classification registry entries"
        indexes = [models.Index(fields=["scheme", "company_name_normalised", "-list_as_of"])]
        constraints = [
            models.UniqueConstraint(
                fields=["scheme", "company_name", "list_as_of"],
                name="uniq_registry_entry_per_list",
            )
        ]

    def __str__(self):
        return f"{self.company_name} {self.notation} @ {self.list_as_of}"


class DebarmentEntry(TimeStampedModel):
    """
    Exclusion lists.

    The World Bank and AfDB lists are public and machine-checkable, and being
    on either bars a firm from IFI-funded work in Ghana.  A public PPA
    debarment list has not been located, so PPA entries are entered by hand
    when a circular surfaces one.
    """

    class Authority(models.TextChoices):
        PPA = "PPA", "Public Procurement Authority (Ghana)"
        WORLD_BANK = "WORLD_BANK", "World Bank"
        AFDB = "AFDB", "African Development Bank"
        OTHER_MDB = "OTHER_MDB", "Other MDB (cross-debarment)"

    authority = models.CharField(max_length=20, choices=Authority.choices)
    company_name = models.CharField(max_length=255, db_index=True)
    company_name_normalised = models.CharField(max_length=255, blank=True, db_index=True)
    country = models.CharField(max_length=64, blank=True)
    from_date = models.DateField(null=True, blank=True)
    to_date = models.DateField(null=True, blank=True)
    reason = models.TextField(blank=True)
    source_url = models.URLField(max_length=500, blank=True)

    class Meta:
        ordering = ["-from_date"]
        verbose_name_plural = "debarment entries"

    def is_active_on(self, on_date) -> bool:
        if self.from_date and on_date < self.from_date:
            return False
        if self.to_date and on_date > self.to_date:
            return False
        return True
