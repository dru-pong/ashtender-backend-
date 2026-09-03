"""
Global reference data.  No tenant column anywhere in this module.

Everything here is either published by a Ghanaian authority or derived from
open data, and every tenant sees the same rows.  Spec section 5.2.
"""
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from treebeard.mp_tree import MP_Node

from core.enums import (
    Channel,
    EntityCategory,
    ObjectType,
    RequirementTier,
    SourceTier,
    ValidityMode,
    VerificationMethod,
)
from core.models import TimeStampedModel


class Region(models.Model):
    """The 16 regions as constituted after the 2018/19 creation exercise."""

    code = models.CharField(max_length=8, unique=True)
    name = models.CharField(max_length=64, unique=True)
    capital = models.CharField(max_length=64, blank=True)
    district_seed_complete = models.BooleanField(
        default=False,
        help_text="False until every MMDA in this region has been loaded from a "
        "published source. Anything that filters tenders by district must say "
        "so when this is False, rather than reporting a partial catalogue as "
        "if it were the full 261.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class District(models.Model):
    """
    MMDAs -- metropolitan, municipal and district assemblies.

    There are 261.  The seed ships the subset confirmed against a published
    source; ``load_districts`` ingests the rest from CSV rather than having
    them invented.  ``is_seeded_complete`` on Region records which regions are
    fully loaded, so an incomplete catalogue cannot masquerade as authoritative.
    """

    class Kind(models.TextChoices):
        METROPOLITAN = "METROPOLITAN", "Metropolitan"
        MUNICIPAL = "MUNICIPAL", "Municipal"
        DISTRICT = "DISTRICT", "District"

    region = models.ForeignKey(Region, on_delete=models.PROTECT, related_name="districts")
    name = models.CharField(max_length=128)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.DISTRICT)
    capital = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["region__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["region", "name"], name="uniq_district_per_region")
        ]

    def __str__(self):
        return f"{self.name} ({self.region.code})"


class ClassificationScheme(models.Model):
    """
    Two ministries run two separate contractor-classification schemes and they
    are not interchangeable (spec section 3.7).  A firm classified A1B1 by
    Roads & Highways holds nothing at all in the Works & Housing D/K/E/G system.
    """

    class Code(models.TextChoices):
        MWHWR = "MWHWR", "Ministry of Works, Housing & Water Resources"
        MRH = "MRH", "Ministry of Roads & Highways"
        ENERGY_COMMISSION = "ENERGY_COMMISSION", "Energy Commission"

    code = models.CharField(max_length=32, choices=Code.choices, unique=True)
    name = models.CharField(max_length=160)
    register_url = models.URLField(blank=True)
    register_ingestible = models.BooleanField(
        default=False,
        help_text="True only if the published register is machine-readable. "
        "Both ministry registers are PDF, so this is False and verification "
        "falls back to VLM extraction plus human confirmation.",
    )
    publication_cadence = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.code


class WorkCategory(models.Model):
    """
    A classification letter within a scheme.

    ``max_financial_class`` is required and must not be assumed to be 4:
    MWHWR runs E on 1-3 and G on 1-2 (spec section 3.7).  Hard-coding 1-4
    would silently invent classes that cannot exist.
    """

    scheme = models.ForeignKey(
        ClassificationScheme, on_delete=models.CASCADE, related_name="categories"
    )
    letter = models.CharField(max_length=2)
    name = models.CharField(max_length=160)
    max_financial_class = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(9)],
        help_text="Numerically largest (i.e. lowest-capacity) class that exists "
        "for this letter. Class 1 is always the highest capacity.",
    )
    object_type = models.CharField(
        max_length=20, choices=ObjectType.choices, default=ObjectType.WORKS
    )

    class Meta:
        ordering = ["scheme__code", "letter"]
        verbose_name_plural = "work categories"
        constraints = [
            models.UniqueConstraint(fields=["scheme", "letter"], name="uniq_category_per_scheme")
        ]

    def __str__(self):
        return f"{self.scheme.code}:{self.letter}"

    def class_exists(self, financial_class: int) -> bool:
        return 1 <= int(financial_class) <= self.max_financial_class


class FinancialClassCap(models.Model):
    """
    Maximum contract value a holder of (scheme, category, class) may bid.

    The circulating figures (class 4 <= USD 75k, 3 <= 200k, 2 <= 500k,
    1 unlimited) trace to Eyiah 2004 / Dansoh 2005 with no current primary
    confirmation of either the amounts or the currency.  They are therefore
    seeded with ``is_stale=True``, and the eligibility engine treats any tender
    within the boundary band of a stale cap as UNKNOWN rather than FAIL.
    """

    scheme = models.ForeignKey(ClassificationScheme, on_delete=models.CASCADE)
    category = models.ForeignKey(
        WorkCategory, on_delete=models.CASCADE, null=True, blank=True,
        help_text="Null means the cap applies to every category in the scheme.",
    )
    financial_class = models.PositiveSmallIntegerField()
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Null means unlimited (class 1 under the legacy figures).",
    )
    currency = models.CharField(max_length=3, default="USD")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_stale = models.BooleanField(
        default=True,
        help_text="True until confirmed against a current primary source. "
        "Stale caps may never produce a FAIL on their own.",
    )
    source_note = models.TextField(blank=True)

    class Meta:
        ordering = ["scheme__code", "financial_class"]
        constraints = [
            models.UniqueConstraint(
                fields=["scheme", "category", "financial_class", "effective_from"],
                name="uniq_cap_per_class_period",
            )
        ]

    def __str__(self):
        cat = self.category.letter if self.category else "*"
        amount = "unlimited" if self.amount is None else f"{self.currency} {self.amount:,.0f}"
        return f"{self.scheme.code}:{cat}{self.financial_class} <= {amount}"


class FxRate(models.Model):
    """
    Dated Bank of Ghana rates.

    Conversion convention, fixed once so verdicts are reproducible: a tender is
    compared against a cap using the rate effective on the tender's publication
    date, never today's rate.  A verdict must not change because the cedi moved.
    """

    currency_pair = models.CharField(max_length=7, help_text="e.g. USDGHS")
    rate = models.DecimalField(max_digits=18, decimal_places=6)
    as_of = models.DateField()
    source = models.CharField(max_length=64, default="BOG")

    class Meta:
        ordering = ["-as_of"]
        constraints = [
            models.UniqueConstraint(
                fields=["currency_pair", "as_of", "source"], name="uniq_fx_per_day"
            )
        ]
        indexes = [models.Index(fields=["currency_pair", "-as_of"])]

    def __str__(self):
        return f"{self.currency_pair} {self.rate} @ {self.as_of}"


class ProcurementDomain(models.Model):
    """Breadth bucket, mappable onto aggregator categories.  ~22 of them."""

    code = models.SlugField(max_length=48, unique=True)
    name = models.CharField(max_length=120)
    object_type = models.CharField(max_length=20, choices=ObjectType.choices)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Discipline(MP_Node):
    """
    Deep taxonomy of what the work actually is.

    Mined from real GHANEPS/OCDS tender titles rather than invented, because an
    invented tree encodes what a developer imagined Ghana procures instead of
    what it procures (spec sections 4.1, 11 phase 4).
    """

    slug = models.SlugField(max_length=120, unique=True)
    name = models.CharField(max_length=160)
    domain = models.ForeignKey(
        ProcurementDomain, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="disciplines",
    )
    ocds_title_count = models.PositiveIntegerField(
        default=0, help_text="How many mined OCDS titles landed on this node. Evidence of coverage."
    )
    node_order_by = ["name"]

    def __str__(self):
        return self.name


class DisciplineCategoryRule(models.Model):
    """Which classification letters a discipline requires."""

    discipline = models.ForeignKey(
        Discipline, on_delete=models.CASCADE, related_name="category_rules"
    )
    category = models.ForeignKey(WorkCategory, on_delete=models.CASCADE)
    is_mandatory = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["discipline", "category"], name="uniq_rule_per_discipline_category"
            )
        ]


class CredentialAuthority(models.Model):
    """Body that issues a document a tender can demand."""

    code = models.SlugField(max_length=48, unique=True)
    name = models.CharField(max_length=160)
    register_url = models.URLField(blank=True)
    register_ingestible = models.BooleanField(default=False)
    contact_phone = models.CharField(max_length=64, blank=True)
    contact_address = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "credential authorities"

    def __str__(self):
        return self.code


class RequirementSpec(models.Model):
    """
    One thing a tender can require, expressed so validity is computable.

    ``validity_mode`` is what makes "is this still good on closing day?" a
    function rather than a judgement call.
    """

    code = models.SlugField(max_length=64, unique=True)
    authority = models.ForeignKey(
        CredentialAuthority, on_delete=models.PROTECT, related_name="specs"
    )
    doc_type = models.CharField(max_length=64)
    name = models.CharField(max_length=160)
    validity_mode = models.CharField(max_length=20, choices=ValidityMode.choices)
    recency_months = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Required only when validity_mode is ISSUE_RECENCY."
    )
    verification_method = models.CharField(
        max_length=20, choices=VerificationMethod.choices, default=VerificationMethod.DOCUMENT
    )
    statutory_reference = models.CharField(max_length=255, blank=True)
    is_conditional = models.BooleanField(
        default=False, help_text='e.g. VAT registration applies only "if applicable".'
    )
    condition_note = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.code

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.validity_mode == ValidityMode.ISSUE_RECENCY and not self.recency_months:
            raise ValidationError({"recency_months": "Required when validity_mode is ISSUE_RECENCY."})


class DomainRequirementRule(models.Model):
    domain = models.ForeignKey(
        ProcurementDomain, on_delete=models.CASCADE, related_name="requirement_rules"
    )
    spec = models.ForeignKey(RequirementSpec, on_delete=models.CASCADE)
    tier = models.CharField(
        max_length=20, choices=RequirementTier.choices, default=RequirementTier.DOMAIN
    )
    is_mandatory = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["domain", "spec"], name="uniq_rule_per_domain_spec")
        ]


class ProcuringEntity(models.Model):
    """A buyer.  Ministries, MMDAs, VRA, COCOBOD, GHA, universities, UN, INGOs."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    channel = models.CharField(max_length=20, choices=Channel.choices)
    entity_category = models.CharField(
        max_length=2, choices=EntityCategory.choices, blank=True,
        help_text="A-E band from L.I. 2516 2nd Schedule; drives approving authority.",
    )
    region = models.ForeignKey(Region, on_delete=models.SET_NULL, null=True, blank=True)
    district = models.ForeignKey(District, on_delete=models.SET_NULL, null=True, blank=True)
    parent_ministry = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="agencies"
    )
    website = models.URLField(blank=True)
    ocds_party_id = models.CharField(max_length=128, blank=True, db_index=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "procuring entities"

    def __str__(self):
        return self.name


class EntityContact(TimeStampedModel):
    """
    A named officer at a buyer.

    Highest-confidence tier is NOTICE_TEXT: the notice already names an officer
    with a title, postal address and telephone number, and explicitly invites
    enquiries.  That is an extraction task on text already held, not a scraping
    problem (spec section 9).

    ``verified_by_human`` is never overwritten by an automated pass.
    """

    entity = models.ForeignKey(ProcuringEntity, on_delete=models.CASCADE, related_name="contacts")
    full_name = models.CharField(max_length=160)
    title = models.CharField(max_length=160, blank=True)
    phones = models.JSONField(default=list, blank=True)
    email = models.EmailField(blank=True)
    postal_address = models.CharField(max_length=255, blank=True)
    source_tier = models.CharField(max_length=20, choices=SourceTier.choices)
    source_url = models.URLField(blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    verified_by_human = models.BooleanField(default=False)

    class Meta:
        ordering = ["entity__name", "full_name"]

    def __str__(self):
        return f"{self.full_name} ({self.entity.name})"


class ProcurementPlanItem(TimeStampedModel):
    """
    A line from an entity's Annual Procurement Plan.

    What a buyer *intends* to procure, months before any notice exists.  This
    is the systematisable version of "having a contact": it lets a tenant open
    a relationship in February about a July advertisement (spec section 9.3).
    """

    entity = models.ForeignKey(ProcuringEntity, on_delete=models.CASCADE, related_name="plan_items")
    fiscal_year = models.PositiveSmallIntegerField()
    quarter = models.PositiveSmallIntegerField(null=True, blank=True)
    description = models.TextField()
    object_type = models.CharField(max_length=20, choices=ObjectType.choices, blank=True)
    value_band_low = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    value_band_high = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="GHS")
    funding_source = models.CharField(max_length=20, blank=True)
    source_url = models.URLField(blank=True)

    class Meta:
        ordering = ["-fiscal_year", "entity__name"]
        indexes = [models.Index(fields=["fiscal_year", "quarter"])]
