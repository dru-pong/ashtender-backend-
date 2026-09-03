"""
Seed the global reference catalogue.  Idempotent: safe to re-run on every deploy.

What is *not* here is as deliberate as what is.  There are 261 MMDAs and this
command seeds only the ones confirmed against a published source, leaving
``Region.district_seed_complete`` False.  Inventing the remainder from memory
would produce a catalogue that looks authoritative and quietly mismatches
district names in real notices -- worse than an obviously incomplete one.
Load the rest with ``manage.py load_districts <csv>``.
"""
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import (
    ClassificationScheme,
    CredentialAuthority,
    District,
    DomainRequirementRule,
    FinancialClassCap,
    FxRate,
    ProcurementDomain,
    Region,
    RequirementSpec,
    WorkCategory,
)
from core.enums import ObjectType, RequirementTier, ValidityMode, VerificationMethod

REGIONS = [
    ("AH", "Ahafo", "Goaso"),
    ("AS", "Ashanti", "Kumasi"),
    ("BO", "Bono", "Sunyani"),
    ("BE", "Bono East", "Techiman"),
    ("CP", "Central", "Cape Coast"),
    ("EP", "Eastern", "Koforidua"),
    ("GA", "Greater Accra", "Accra"),
    ("NE", "North East", "Nalerigu"),
    ("NP", "Northern", "Tamale"),
    ("OT", "Oti", "Dambai"),
    ("SV", "Savannah", "Damongo"),
    ("UE", "Upper East", "Bolgatanga"),
    ("UW", "Upper West", "Wa"),
    ("TV", "Volta", "Ho"),
    ("WP", "Western", "Sekondi-Takoradi"),
    ("WN", "Western North", "Sefwi Wiawso"),
]

# Ahafo is seeded in full because tenant zero's known contract is in Bechem,
# capital of Tano South.  Everything else is partial by design.
DISTRICTS = [
    ("AH", "Asunafo North Municipal", District.Kind.MUNICIPAL, "Goaso"),
    ("AH", "Asunafo South", District.Kind.DISTRICT, "Kukuom"),
    ("AH", "Asutifi North", District.Kind.DISTRICT, "Kenyasi"),
    ("AH", "Asutifi South", District.Kind.DISTRICT, "Hwidiem"),
    ("AH", "Tano North Municipal", District.Kind.MUNICIPAL, "Duayaw Nkwanta"),
    ("AH", "Tano South Municipal", District.Kind.MUNICIPAL, "Bechem"),
    ("BO", "Sunyani Municipal", District.Kind.MUNICIPAL, "Sunyani"),
    ("BO", "Sunyani West Municipal", District.Kind.MUNICIPAL, "Odumase"),
    ("GA", "Accra Metropolitan", District.Kind.METROPOLITAN, "Accra"),
    ("AS", "Kumasi Metropolitan", District.Kind.METROPOLITAN, "Kumasi"),
]

SCHEMES = [
    (
        ClassificationScheme.Code.MWHWR,
        "Ministry of Works, Housing & Water Resources",
        "https://mwh.gov.gh/",
        "Monthly list of contractors in good standing",
        "Classification Unit, Room 39, 1st Floor, P.O. Box M43, Ministries, Accra. "
        "Tel +233 577902988 / +233 577902933. Services: new/upgrading and annual renewal.",
    ),
    (
        ClassificationScheme.Code.MRH,
        "Ministry of Roads & Highways",
        "https://mrh.gov.gh/publications/contractor-classification/",
        "Periodic reclassification notices",
        "Forms from MRH Accounts Department or designated banks; online submission at "
        "mrh.gov.gh. Licence issues on fee payment within 21 days; unpaid applications lapse.",
    ),
    (
        ClassificationScheme.Code.ENERGY_COMMISSION,
        "Energy Commission",
        "https://energycom.gov.gh/",
        "Unconfirmed",
        "Electrical contractor licensing. Register format not yet confirmed.",
    ),
]

# max_financial_class is per letter and must not be assumed to be 4.
CATEGORIES = [
    ("MWHWR", "D", "General building works", 4),
    ("MWHWR", "K", "General civil works", 4),
    ("MWHWR", "E", "Electrical works", 3),
    ("MWHWR", "G", "Plumbing works", 2),
    ("MRH", "A", "Roads, airports and related structures", 4),
    ("MRH", "B", "Bridges, culverts and related structures", 4),
    ("MRH", "C", "Labour-based road works", 4),
    ("MRH", "S", "Steel bridges", 4),
]

STALE_CAP_NOTE = (
    "Figures trace to Eyiah (2004) / Dansoh (2005). No current primary source "
    "confirms either the amounts or that the currency is still USD. Seeded "
    "is_stale=True: these may send a tender to a human but may never fail one. "
    "Resolution path: MWHWR Classification Unit and the MRH Road Classification "
    "Secretariat (see docs/OPEN_QUESTIONS.md)."
)

LEGACY_CAPS = [(4, "75000"), (3, "200000"), (2, "500000"), (1, None)]

AUTHORITIES = [
    ("gra", "Ghana Revenue Authority", "https://gra.gov.gh/", False, "", ""),
    ("ssnit", "Social Security and National Insurance Trust", "https://ssnit.org.gh/", False, "", ""),
    ("orc", "Office of the Registrar of Companies", "https://orc.gov.gh/service/entity-information-search/",
     False, "+233 30 266 6081", "P.O. Box 118 / P.O. Box 2559, Accra. Entity search is paid."),
    ("mwhwr", "Ministry of Works, Housing & Water Resources", "https://mwh.gov.gh/", False,
     "+233 577902988", "Classification Unit, Room 39, 1st Floor, P.O. Box M43, Ministries, Accra"),
    ("mrh", "Ministry of Roads & Highways", "https://mrh.gov.gh/publications/contractor-classification/",
     False, "", "Road Classification Committee"),
    ("labour", "Labour Department", "", False, "", ""),
    ("ppa", "Public Procurement Authority", "https://ppa.gov.gh/", False, "",
     "5th Floor, Tower 1, Ministry of Finance, Tumu Avenue, Kanda, Accra. "
     "Local supplier registration GHS 500, annual renewal GHS 300."),
    ("mmda", "District / Municipal / Metropolitan Assembly", "", False, "",
     "Issues the Business Operating Permit. Observed in practice though absent "
     "from the Medium Works STD statutory list."),
    ("bank", "Commercial bank", "", False, "", ""),
    ("auditor", "External auditor", "", False, "", ""),
    ("dpc", "Data Protection Commission", "https://www.dataprotection.org.gh/", False,
     "+233 256301533", "Registration of data controllers under Act 843 s.27(1)."),
]

# The statutory pack, verbatim from the Medium Works STD, Invitation for
# Tenders para 7, plus the Business Operating Permit which is universally
# demanded in practice but is not in that list.
STATUTORY_SPECS = [
    ("tax-clearance", "gra", "TAX_CLEARANCE", "GRA Tax Clearance Certificate",
     ValidityMode.PRINTED_EXPIRY, None, VerificationMethod.DOCUMENT,
     "Medium Works STD, Invitation for Tenders para 7", False, ""),
    ("ssnit-clearance", "ssnit", "SSNIT_CLEARANCE", "SSNIT Clearance Certificate",
     ValidityMode.PRINTED_EXPIRY, None, VerificationMethod.DOCUMENT,
     "Medium Works STD, Invitation for Tenders para 7", False, ""),
    ("vat-registration", "gra", "VAT_REGISTRATION", "VAT Registration Certificate",
     ValidityMode.PERPETUAL, None, VerificationMethod.DOCUMENT,
     "Medium Works STD, Invitation for Tenders para 7", True,
     'The STD qualifies this with "if applicable", so it is only required of '
     "VAT-registered firms."),
    ("business-registration", "orc", "BUSINESS_REGISTRATION", "Business Registration Certificate",
     ValidityMode.PERPETUAL, None, VerificationMethod.DOCUMENT,
     "Medium Works STD, Invitation for Tenders para 7", False, ""),
    ("certificate-incorporation", "orc", "CERT_INCORPORATION", "Certificate of Incorporation",
     ValidityMode.PERPETUAL, None, VerificationMethod.DOCUMENT,
     "Medium Works STD, Invitation for Tenders para 7", False, ""),
    ("certificate-commencement", "orc", "CERT_COMMENCEMENT", "Certificate of Commencement of Business",
     ValidityMode.PERPETUAL, None, VerificationMethod.DOCUMENT,
     "Medium Works STD, Invitation for Tenders para 7", False, ""),
    ("works-housing-certificate", "mwhwr", "CLASSIFICATION_CERT", "Works & Housing Certificate",
     ValidityMode.ANNUAL_RENEWAL, None, VerificationMethod.REGISTER,
     "Medium Works STD, Invitation for Tenders para 7", False,
     "Verify against the ministry register rather than the certificate image "
     "wherever the register covers the firm."),
    ("labour-certificate", "labour", "LABOUR_CERT", "Labour Certificate",
     ValidityMode.ANNUAL_RENEWAL, None, VerificationMethod.DOCUMENT,
     "Medium Works STD, Invitation for Tenders para 7", False, ""),
    ("ppa-registration", "ppa", "PPA_REGISTRATION", "PPA Registration Certificate",
     ValidityMode.ANNUAL_RENEWAL, None, VerificationMethod.DOCUMENT,
     "Medium Works STD, Invitation for Tenders para 7", False,
     "Registration GHS 500, annual renewal GHS 300 as cited by PPA."),
    ("business-operating-permit", "mmda", "BOP", "Business Operating Permit",
     ValidityMode.ANNUAL_RENEWAL, None, VerificationMethod.DOCUMENT,
     "Not in STD para 7; demanded in practice by assemblies", False, ""),
    ("road-classification-certificate", "mrh", "CLASSIFICATION_CERT",
     "MRH Road Contractor Classification Certificate",
     ValidityMode.PRINTED_EXPIRY, None, VerificationMethod.REGISTER,
     "MRH classification regime", False,
     "The published lists carry explicit valid-from / valid-to dates, so "
     "printed expiry rather than annual renewal."),
    ("bank-reference", "bank", "BANK_REFERENCE", "Bank reference / lines of credit",
     ValidityMode.ISSUE_RECENCY, 6, VerificationMethod.DOCUMENT,
     "Section IV, financial standing", False, ""),
    ("audited-accounts", "auditor", "AUDITED_ACCOUNTS", "Audited accounts",
     ValidityMode.ISSUE_RECENCY, 18, VerificationMethod.DOCUMENT,
     "Section IV, five years of profit and loss plus auditor's reports", False, ""),
]

DOMAINS = [
    ("universal-statutory", "Universal statutory pack", ObjectType.WORKS,
     "Carrier for requirements every Ghanaian public tender imposes. Not a real "
     "procurement category; it exists so universal rules have somewhere to hang."),
    ("road-construction", "Road construction and upgrading", ObjectType.WORKS, ""),
    ("road-maintenance", "Road maintenance, reshaping and spot improvement", ObjectType.WORKS, ""),
    ("bridges-culverts", "Bridges and culverts", ObjectType.WORKS, ""),
    ("building-construction", "Building construction", ObjectType.WORKS, ""),
    ("water-sanitation", "Water and sanitation", ObjectType.WORKS, ""),
    ("boreholes", "Boreholes and handpumps", ObjectType.WORKS, ""),
    ("electrical-works", "Electrical installation and reticulation", ObjectType.WORKS, ""),
    ("drainage-flood", "Drainage and flood control", ObjectType.WORKS, ""),
    ("sea-defence", "Sea defence and coastal protection", ObjectType.WORKS, ""),
    ("school-infrastructure", "Educational infrastructure", ObjectType.WORKS, ""),
    ("health-infrastructure", "Health infrastructure", ObjectType.WORKS, ""),
    ("market-infrastructure", "Markets and commercial facilities", ObjectType.WORKS, ""),
    ("supply-goods-general", "General goods supply", ObjectType.GOODS, ""),
    ("supply-ict", "ICT goods and equipment", ObjectType.GOODS, ""),
    ("supply-medical", "Medical supplies and equipment", ObjectType.GOODS, ""),
    ("supply-vehicles", "Vehicles and plant", ObjectType.GOODS, ""),
    ("consultancy-engineering", "Engineering consultancy", ObjectType.CONSULTANCY, ""),
    ("consultancy-management", "Management and financial consultancy", ObjectType.CONSULTANCY, ""),
    ("services-security", "Security services", ObjectType.NON_CONSULTANCY, ""),
    ("services-cleaning", "Cleaning and sanitation services", ObjectType.NON_CONSULTANCY, ""),
    ("services-maintenance", "Equipment maintenance services", ObjectType.NON_CONSULTANCY, ""),
    ("disposal-assets", "Disposal of stores and assets", ObjectType.DISPOSAL, ""),
]

#: Every tender needs these, regardless of what it is for.
UNIVERSAL_SPEC_CODES = [
    "tax-clearance", "ssnit-clearance", "vat-registration", "business-registration",
    "certificate-incorporation", "certificate-commencement", "labour-certificate",
    "ppa-registration", "business-operating-permit",
]

DOMAIN_SPEC_RULES = {
    "road-construction": ["road-classification-certificate", "bank-reference", "audited-accounts"],
    "road-maintenance": ["road-classification-certificate", "audited-accounts"],
    "bridges-culverts": ["road-classification-certificate", "bank-reference", "audited-accounts"],
    "building-construction": ["works-housing-certificate", "bank-reference", "audited-accounts"],
    "water-sanitation": ["works-housing-certificate", "audited-accounts"],
    "boreholes": ["works-housing-certificate", "audited-accounts"],
    "electrical-works": ["works-housing-certificate", "audited-accounts"],
    "drainage-flood": ["works-housing-certificate", "bank-reference", "audited-accounts"],
    "sea-defence": ["works-housing-certificate", "bank-reference", "audited-accounts"],
    "school-infrastructure": ["works-housing-certificate", "bank-reference", "audited-accounts"],
    "health-infrastructure": ["works-housing-certificate", "bank-reference", "audited-accounts"],
    "market-infrastructure": ["works-housing-certificate", "audited-accounts"],
}


class Command(BaseCommand):
    help = "Seed global reference data. Idempotent."

    def add_arguments(self, parser):
        parser.add_argument(
            "--quiet-summary", action="store_true", help="Suppress the closing summary."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        counts = {}

        for code, name, capital in REGIONS:
            Region.objects.update_or_create(
                code=code,
                defaults={"name": name, "capital": capital, "district_seed_complete": False},
            )
        counts["regions"] = Region.objects.count()

        regions = {region.code: region for region in Region.objects.all()}
        for region_code, name, kind, capital in DISTRICTS:
            District.objects.update_or_create(
                region=regions[region_code], name=name,
                defaults={"kind": kind, "capital": capital},
            )
        # Ahafo is the only region seeded exhaustively.
        Region.objects.filter(code="AH").update(district_seed_complete=True)
        counts["districts"] = District.objects.count()

        for code, name, url, cadence, notes in SCHEMES:
            ClassificationScheme.objects.update_or_create(
                code=code,
                defaults={
                    "name": name, "register_url": url, "register_ingestible": False,
                    "publication_cadence": cadence, "notes": notes,
                },
            )
        counts["schemes"] = ClassificationScheme.objects.count()

        schemes = {scheme.code: scheme for scheme in ClassificationScheme.objects.all()}
        for scheme_code, letter, name, max_class in CATEGORIES:
            WorkCategory.objects.update_or_create(
                scheme=schemes[scheme_code], letter=letter,
                defaults={
                    "name": name, "max_financial_class": max_class,
                    "object_type": ObjectType.WORKS,
                },
            )
        counts["work_categories"] = WorkCategory.objects.count()

        for scheme_code in ("MWHWR", "MRH"):
            for financial_class, amount in LEGACY_CAPS:
                FinancialClassCap.objects.update_or_create(
                    scheme=schemes[scheme_code], category=None,
                    financial_class=financial_class, effective_from=date(2004, 1, 1),
                    defaults={
                        "amount": amount, "currency": "USD", "is_stale": True,
                        "source_note": STALE_CAP_NOTE,
                    },
                )
        counts["financial_class_caps"] = FinancialClassCap.objects.count()

        # A single placeholder rate so currency conversion has a code path to
        # exercise.  Replace with real dated BoG rates before relying on any
        # cap comparison.
        FxRate.objects.update_or_create(
            currency_pair="USDGHS", as_of=date(2026, 1, 2), source="PLACEHOLDER",
            defaults={"rate": "12.500000"},
        )
        counts["fx_rates"] = FxRate.objects.count()

        for code, name, url, ingestible, phone, address in AUTHORITIES:
            CredentialAuthority.objects.update_or_create(
                code=code,
                defaults={
                    "name": name, "register_url": url, "register_ingestible": ingestible,
                    "contact_phone": phone, "contact_address": address,
                },
            )
        counts["credential_authorities"] = CredentialAuthority.objects.count()

        authorities = {a.code: a for a in CredentialAuthority.objects.all()}
        for (code, authority_code, doc_type, name, mode, recency, verification,
             reference, conditional, condition_note) in STATUTORY_SPECS:
            RequirementSpec.objects.update_or_create(
                code=code,
                defaults={
                    "authority": authorities[authority_code], "doc_type": doc_type,
                    "name": name, "validity_mode": mode, "recency_months": recency,
                    "verification_method": verification, "statutory_reference": reference,
                    "is_conditional": conditional, "condition_note": condition_note,
                },
            )
        counts["requirement_specs"] = RequirementSpec.objects.count()

        for code, name, object_type, description in DOMAINS:
            ProcurementDomain.objects.update_or_create(
                code=code,
                defaults={"name": name, "object_type": object_type, "description": description},
            )
        counts["domains"] = ProcurementDomain.objects.count()

        domains = {d.code: d for d in ProcurementDomain.objects.all()}
        specs = {s.code: s for s in RequirementSpec.objects.all()}

        universal_domain = domains["universal-statutory"]
        for spec_code in UNIVERSAL_SPEC_CODES:
            DomainRequirementRule.objects.update_or_create(
                domain=universal_domain, spec=specs[spec_code],
                defaults={"tier": RequirementTier.UNIVERSAL, "is_mandatory": True},
            )
        for domain_code, spec_codes in DOMAIN_SPEC_RULES.items():
            for spec_code in spec_codes:
                DomainRequirementRule.objects.update_or_create(
                    domain=domains[domain_code], spec=specs[spec_code],
                    defaults={"tier": RequirementTier.DOMAIN, "is_mandatory": True},
                )
        counts["requirement_rules"] = DomainRequirementRule.objects.count()

        if not options["quiet_summary"]:
            width = max(len(k) for k in counts)
            for key, value in counts.items():
                self.stdout.write(f"  {key.ljust(width)}  {value}")
            incomplete = Region.objects.filter(district_seed_complete=False).count()
            self.stdout.write(
                self.style.WARNING(
                    f"  {incomplete} of {len(REGIONS)} regions still have incomplete "
                    "district lists (261 MMDAs total). Load them with "
                    "'manage.py load_districts <csv>'."
                )
            )
            self.stdout.write(self.style.SUCCESS("catalog seed complete"))
