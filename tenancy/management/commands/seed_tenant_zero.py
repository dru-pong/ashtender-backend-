"""
Seed tenant zero: Shamma Rohi Limited, the design partner.

Every fact below comes from a Ghana government primary source, cited inline.
Nothing is invented, and the gaps are seeded as gaps -- a blank TIN and a blank
ORC number are the true state of knowledge, and filling them with plausible
values would corrupt the only calibration data the engine has.

The most useful thing this fixture produces is a classification that satisfies
the requirement on paper but whose published validity ended on 2 April 2022.
That drives the engine down its most important path: not FAIL, but UNKNOWN with
a named remedy, because ministry lists lag and a firm may well hold a current
certificate that no published list yet reflects.
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from bids.models import ActionRequest
from catalog.models import ClassificationScheme, District, ProcuringEntity, Region, WorkCategory
from core.enums import Channel, ClaimOutcome, FundingSource, IPCStatus, ObjectType
from core.tenancy import active_org
from receivables.models import ArrearsClaim, Contract, IPC
from tenancy.models import CompanyProfile, Organization
from tenders.models import ClassificationRegistryEntry
from vault.models import Classification

MRH_GOOD_STANDING_URL = (
    "https://mrh.gov.gh/wp-content/uploads/2021/08/"
    "List-of-Contractors-in-good-standing-as-of-18th-June-2021.pdf"
)
MRH_RECLASSIFICATION_URL = (
    "https://mrh.gov.gh/wp-content/uploads/2023/02/"
    "PUBLIC-NOTICE-RECLASIFICATION-OF-CONTRACTORS.pdf"
)
AUDITOR_GENERAL_URL = (
    "https://audit.gov.gh/files/audit_reports/"
    "Special_audit_report__of_the_Auditor-General__on_Government_outstanding_"
    "claims_and_commitments_as_at_31_December_2024.pdf"
)

BECHEM_CONTRACT_NAME = (
    "Upgrading of Selected Roads in Bechem Town (The Whole, Kwasu, And Mansen) Phase 2"
)
BECHEM_CLAIM_VALUE = Decimal("12473675.00")


class Command(BaseCommand):
    help = "Seed Shamma Rohi Limited (tenant zero) from published primary sources. Idempotent."

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            mrh_scheme = ClassificationScheme.objects.get(code="MRH")
            category_a = WorkCategory.objects.get(scheme=mrh_scheme, letter="A")
            category_b = WorkCategory.objects.get(scheme=mrh_scheme, letter="B")
        except (ClassificationScheme.DoesNotExist, WorkCategory.DoesNotExist):
            raise CommandError("Run 'manage.py seed_catalog' first.")

        bono = Region.objects.get(code="BO")
        sunyani = District.objects.get(region=bono, name="Sunyani Municipal")
        bechem = District.objects.get(name="Tano South Municipal")

        organization, _ = Organization.objects.update_or_create(
            slug="shamma-rohi",
            defaults={
                "name": "Shamma Rohi Limited",
                "is_active": True,
                "is_design_partner": True,
            },
        )

        profile, _ = CompanyProfile.objects.update_or_create(
            organization=organization,
            defaults={
                "legal_name": "Shamma Rohi Limited",
                # Blank on purpose: not on the free web, behind the paid ORC
                # entity search. See docs/OPEN_QUESTIONS.md item 1.
                "orc_registration_number": "",
                "tin": "",
                "incorporation_date": None,
                "incorporated_in_ghana": True,
                "ghanaian_shareholding_pct": None,
                "postal_address": "P.O. Box 1260, Sunyani",
                "home_region": bono,
                "home_district": sunyani,
                "object_types": [ObjectType.WORKS],
                "min_prep_days": 5,
            },
        )
        profile.service_areas.add(sunyani, bechem)

        # --- the oracle: what the ministry actually published ------------
        good_standing, _ = ClassificationRegistryEntry.objects.update_or_create(
            scheme=mrh_scheme,
            company_name="Shamma Rohi Limited",
            list_as_of=date(2021, 6, 18),
            defaults={
                "company_name_normalised": "SHAMMA ROHI",
                "notation": "A1B1",
                "categories": [{"letter": "A", "class": 1}, {"letter": "B", "class": 1}],
                "postal_address": "Box 1260, Sunyani",
                "list_seq_no": 415,
                "valid_from": date(2020, 4, 2),
                "valid_to": date(2022, 4, 2),
                "status_as_published": ClassificationRegistryEntry.Status.VALID,
                "list_url": MRH_GOOD_STANDING_URL,
            },
        )

        # Absence from the 2023 reclassification list is itself a finding, so
        # the list is recorded even though this firm has no row on it.  A
        # register we have not read and a register that omits the firm are
        # different states and must not look the same in the database.
        ClassificationRegistryEntry.objects.update_or_create(
            scheme=mrh_scheme,
            company_name="__LIST_MARKER__MRH_RECLASSIFICATION_2023",
            list_as_of=date(2023, 1, 1),
            defaults={
                "company_name_normalised": "",
                "notation": "",
                "categories": [],
                "status_as_published": ClassificationRegistryEntry.Status.UNKNOWN,
                "list_url": MRH_RECLASSIFICATION_URL,
                "list_effective_from": date(2023, 1, 1),
                "postal_address": "",
            },
        )

        with active_org(organization):
            for category in (category_a, category_b):
                Classification.objects.update_or_create(
                    organization=organization,
                    scheme=mrh_scheme,
                    category=category,
                    valid_from=date(2020, 4, 2),
                    defaults={
                        "financial_class": 1,
                        "valid_to": date(2022, 4, 2),
                        "issued_on": date(2020, 4, 2),
                        # Verified against the 2021 list, but that list is not
                        # current.  The engine reads the lapsed window, not this
                        # timestamp, when deciding currency.
                        "verified_against_register_at": timezone.make_aware(
                            timezone.datetime(2021, 6, 18, 12, 0)
                        ),
                        "register_entry": good_standing,
                        "notes": (
                            "A1B1 per MRH list of contractors in good standing as at "
                            "18 June 2021, entry 415. Validity ended 2 April 2022 and "
                            "the firm does not appear on the reclassification list "
                            "effective 1 January 2023. Treat as lapsed pending fresh "
                            "confirmation from the MRH Road Classification Committee."
                        ),
                    },
                )

            entity, _ = ProcuringEntity.objects.update_or_create(
                slug="ministry-of-roads-and-highways",
                defaults={
                    "name": "Ministry of Roads and Highways",
                    "channel": Channel.GOV_ENTITY,
                    "website": "https://mrh.gov.gh/",
                },
            )

            contract, _ = Contract.objects.update_or_create(
                organization=organization,
                name=BECHEM_CONTRACT_NAME,
                defaults={
                    "entity": entity,
                    "funding_source": FundingSource.GOG,
                    "value": BECHEM_CLAIM_VALUE,
                    "currency": "GHS",
                    "district": bechem,
                    "is_complete": True,
                    "notes": (
                        "Recorded against this firm in the Special Audit Report of the "
                        "Auditor-General on Government outstanding claims and "
                        "commitments as at 31 December 2024. The figure is a submitted "
                        "claim or commitment amount, not a confirmed clean payment: the "
                        "2025 validation exercise (Ghana Audit Service with PwC and EY) "
                        "rejected roughly GH¢12.1bn of about GH¢90bn in payables, "
                        "including GH¢125.65m of duplicated MRH claims. Nothing specific "
                        "to this firm was found either way."
                    ),
                },
            )

            # One IPC standing for the whole claim, because the certificate-level
            # breakdown is not public.  Dates are null rather than estimated:
            # PayerScore is built from real submitted/certified/paid dates, and a
            # guessed date would poison the only metric that cannot be replicated.
            ipc, _ = IPC.objects.update_or_create(
                organization=organization,
                contract=contract,
                number="AG-2024-CLAIM",
                defaults={
                    "amount_claimed": BECHEM_CLAIM_VALUE,
                    "amount_certified": BECHEM_CLAIM_VALUE,
                    "amount_paid": None,
                    "currency": "GHS",
                    "submitted_on": None,
                    "certified_on": None,
                    "paid_on": None,
                    "status": IPCStatus.SUBMITTED,
                    "notes": (
                        "Placeholder for the amount appearing in the Auditor-General's "
                        "outstanding-claims schedule. Replace with the real IPC "
                        "sequence, and their submitted/certified/paid dates, from the "
                        "firm's own records."
                    ),
                },
            )

            claim, _ = ArrearsClaim.objects.update_or_create(
                organization=organization,
                reference="AG-OUTSTANDING-CLAIMS-2024-12-31",
                defaults={
                    "exercise_name": (
                        "Auditor-General special audit of government outstanding claims "
                        "and commitments as at 31 December 2024"
                    ),
                    "amount_submitted": BECHEM_CLAIM_VALUE,
                    "amount_validated": None,
                    "outcome": ClaimOutcome.UNKNOWN,
                    "rejection_reason": "",
                },
            )
            claim.ipcs.add(ipc)

            # The two questions no amount of desk research answers.
            ActionRequest.objects.update_or_create(
                organization=organization,
                kind=ActionRequest.Kind.VERIFY_CLASSIFICATION,
                title="Confirm current MRH classification for Shamma Rohi Limited",
                defaults={
                    "status": ActionRequest.Status.OPEN,
                    "priority": 100,
                    "brief": (
                        "BLOCKS eligibility calibration.\n\n"
                        "Published position: A1B1 (category A roads/airports, category B "
                        "bridges/culverts, financial class 1 in both), valid 02-Apr-2020 "
                        "to 02-Apr-2022, entry 415 on the MRH list of contractors in good "
                        "standing as at 18 June 2021. The firm is NOT among the 233 "
                        "contractors on the reclassification notice effective "
                        "1 January 2023.\n\n"
                        "Ask: is there a physical MRH classification certificate dated "
                        "after 1 January 2023? If so, which letters and classes, and what "
                        "valid-from and valid-to dates are printed on it?\n\n"
                        "Contact: MRH Road Classification Committee. Lists are published "
                        f"at {MRH_RECLASSIFICATION_URL}\n\n"
                        "Until this is answered every road tender for this tenant returns "
                        "UNKNOWN on classification, which is correct but not useful."
                    ),
                },
            )
            ActionRequest.objects.update_or_create(
                organization=organization,
                kind=ActionRequest.Kind.CALL_ENTITY,
                title="Establish the real IPC history behind the Bechem claim",
                defaults={
                    "status": ActionRequest.Status.OPEN,
                    "priority": 90,
                    "brief": (
                        f"The Auditor-General's schedule records GH¢{BECHEM_CLAIM_VALUE:,.2f} "
                        f"against '{BECHEM_CONTRACT_NAME}'.\n\n"
                        "Ask the firm for: the individual IPCs behind that figure, and for "
                        "each one the date submitted, the date certified, the date paid "
                        "and the amount paid. Also the oldest still-uncertified IPC across "
                        "all contracts.\n\n"
                        "Those dates are the input to PayerScore. Nothing else in the "
                        "system can be derived from public data and this cannot."
                    ),
                },
            )

        self.stdout.write(f"organization      {organization.name} (id={organization.pk})")
        self.stdout.write(f"profile           {profile.legal_name}, {profile.postal_address}")
        self.stdout.write(
            f"classifications   {Classification.objects.for_org(organization.pk).count()} "
            "(A1, B1 - both lapsed 02-Apr-2022)"
        )
        self.stdout.write(f"contracts         {Contract.objects.for_org(organization.pk).count()}")
        self.stdout.write(
            f"outstanding       GHS {contract.outstanding:,.2f} on the Bechem claim"
        )
        self.stdout.write(
            f"action requests   {ActionRequest.objects.for_org(organization.pk).count()} open"
        )
        self.stdout.write(
            self.style.WARNING(
                "TIN, ORC number, incorporation date and shareholding are blank "
                "because they are not on the free web. Do not fill them with guesses."
            )
        )
        self.stdout.write(self.style.SUCCESS("tenant zero seeded"))
