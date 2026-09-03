"""Organizations, memberships, and the company profile the engine reasons over."""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from core.models import TimeStampedModel


class Organization(TimeStampedModel):
    """
    A tenant.  Deliberately *not* TenantOwned -- it is the tenant.

    Design partner / tenant zero is Shamma Rohi Limited (Sunyani road and
    bridge contractor).  ``is_design_partner`` marks tenants whose data is used
    to calibrate the engine, so calibration never silently generalises from one
    firm's paperwork.
    """

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    is_design_partner = models.BooleanField(default=False)
    onboarded_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Membership(TimeStampedModel):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        BID_MANAGER = "BID_MANAGER", "Bid manager"
        FINANCE = "FINANCE", "Finance"
        VIEWER = "VIEWER", "Viewer"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="uniq_membership")
        ]

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.role})"


class CompanyProfile(TimeStampedModel):
    """
    The legal identity the eligibility engine tests.

    Ownership fields exist because domestic preference is computable, not
    rhetorical: Act 914 defines a domestic Works contractor as (a) registered
    and incorporated under Ghana law, (b) majority Ghanaian shareholding, and
    (c) not subcontracting more than 50% of total Works value to foreign
    contractors.  (a) and (b) live here; (c) is per-bid and lives on ``Bid``.
    """

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="company_profile"
    )
    legal_name = models.CharField(max_length=255)
    trading_name = models.CharField(max_length=255, blank=True)

    # --- registry identity ---------------------------------------------
    orc_registration_number = models.CharField(
        max_length=64, blank=True,
        help_text="Office of the Registrar of Companies number. Behind a paid "
        "ORC entity search, so often blank until someone pays for the lookup.",
    )
    incorporation_date = models.DateField(null=True, blank=True)
    tin = models.CharField(max_length=32, blank=True, verbose_name="TIN")
    ssnit_employer_number = models.CharField(max_length=32, blank=True)
    vat_registered = models.BooleanField(default=False)
    ppa_supplier_number = models.CharField(max_length=64, blank=True)

    # --- domestic-contractor test --------------------------------------
    incorporated_in_ghana = models.BooleanField(default=True)
    ghanaian_shareholding_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    ownership_structure = models.JSONField(
        default=list, blank=True,
        help_text="[{name, nationality, pct, is_beneficial_owner}]. Feeds the "
        "majority-Ghanaian test and mining local-content rules (L.I. 2431).",
    )

    # --- contact / footprint -------------------------------------------
    postal_address = models.CharField(max_length=255, blank=True)
    street_address = models.CharField(max_length=255, blank=True)
    digital_address = models.CharField(max_length=32, blank=True)
    phones = models.JSONField(default=list, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    home_region = models.ForeignKey(
        "catalog.Region", on_delete=models.SET_NULL, null=True, blank=True
    )
    home_district = models.ForeignKey(
        "catalog.District", on_delete=models.SET_NULL, null=True, blank=True
    )
    service_areas = models.ManyToManyField(
        "catalog.District", blank=True, related_name="serving_companies"
    )

    # --- screening preferences -----------------------------------------
    object_types = models.JSONField(
        default=list, blank=True,
        help_text="ObjectType values this firm will bid. First deterministic screen.",
    )
    min_prep_days = models.PositiveSmallIntegerField(
        default=5,
        help_text="Below this many days to closing, a tender is screened out as "
        "late rather than reported as eligible. Tender windows run 7-10 days.",
    )
    min_contract_value = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    working_capital = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Feeds the bid/no-bid trade-off against expected days to payment.",
    )

    def __str__(self):
        return self.legal_name

    @property
    def is_domestic_contractor_by_ownership(self) -> bool | None:
        """
        Limbs (a) and (b) of the Act 914 domestic-contractor test.

        Returns None when shareholding is unrecorded, because an unknown
        ownership split must not be reported as a failed test.
        """
        if not self.incorporated_in_ghana:
            return False
        if self.ghanaian_shareholding_pct is None:
            return None
        return self.ghanaian_shareholding_pct > Decimal("50")
