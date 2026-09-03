"""
Test fixtures.  Plain functions, no factory_boy: the graph is small and an
explicit builder is easier to read than a DSL when a test fails.
"""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.utils import timezone

from catalog.models import (
    ClassificationScheme,
    CredentialAuthority,
    District,
    DomainRequirementRule,
    FinancialClassCap,
    ProcurementDomain,
    ProcuringEntity,
    Region,
    RequirementSpec,
    WorkCategory,
)
from core.enums import (
    Channel,
    ObjectType,
    RequirementTier,
    ValidityMode,
)
from tenancy.models import CompanyProfile, Membership, Organization
from tenders.models import Tender, TenderClassRequirement
from vault.models import Classification, Document, FinancialYear


def make_region(code="BO", name="Bono"):
    return Region.objects.get_or_create(code=code, defaults={"name": name})[0]


def make_district(region=None, name="Sunyani Municipal"):
    region = region or make_region()
    return District.objects.get_or_create(region=region, name=name)[0]


def make_scheme(code="MRH", name="Ministry of Roads & Highways"):
    return ClassificationScheme.objects.get_or_create(code=code, defaults={"name": name})[0]


def make_category(scheme=None, letter="A", max_class=4):
    scheme = scheme or make_scheme()
    return WorkCategory.objects.get_or_create(
        scheme=scheme, letter=letter,
        defaults={"name": f"Category {letter}", "max_financial_class": max_class},
    )[0]


def make_org(name="Test Contractor Ltd", slug=None, object_types=None, min_prep_days=5):
    slug = slug or name.lower().replace(" ", "-")
    org = Organization.objects.create(name=name, slug=slug)
    CompanyProfile.objects.create(
        organization=org,
        legal_name=name,
        object_types=object_types if object_types is not None else [ObjectType.WORKS],
        min_prep_days=min_prep_days,
        incorporated_in_ghana=True,
    )
    return org


def make_user(username="tester", org=None, role=Membership.Role.OWNER):
    user = User.objects.create_user(username=username, password="pw")
    if org is not None:
        Membership.objects.create(organization=org, user=user, role=role)
    return user


def make_entity(name="Ministry of Roads and Highways", slug="mrh"):
    return ProcuringEntity.objects.get_or_create(
        slug=slug, defaults={"name": name, "channel": Channel.GOV_ENTITY}
    )[0]


def make_domain(code="road-construction", name="Road construction"):
    return ProcurementDomain.objects.get_or_create(
        code=code, defaults={"name": name, "object_type": ObjectType.WORKS}
    )[0]


def make_spec(code="tax-clearance", validity_mode=ValidityMode.PRINTED_EXPIRY,
              recency_months=None, authority_code="gra", conditional=False):
    authority = CredentialAuthority.objects.get_or_create(
        code=authority_code, defaults={"name": authority_code.upper()}
    )[0]
    return RequirementSpec.objects.get_or_create(
        code=code,
        defaults={
            "authority": authority, "doc_type": code.upper().replace("-", "_"),
            "name": code.replace("-", " ").title(), "validity_mode": validity_mode,
            "recency_months": recency_months, "is_conditional": conditional,
        },
    )[0]


def attach_universal(spec):
    domain = make_domain("universal-statutory", "Universal statutory pack")
    DomainRequirementRule.objects.get_or_create(
        domain=domain, spec=spec,
        defaults={"tier": RequirementTier.UNIVERSAL, "is_mandatory": True},
    )
    return domain


def make_tender(entity=None, *, days_to_close=30, value=None, currency="GHS",
                object_type=ObjectType.WORKS, domain=None, reference="REF-001",
                title="Upgrading of Selected Roads"):
    entity = entity or make_entity()
    now = timezone.now()
    return Tender.objects.create(
        entity=entity, reference_number=reference, title=title,
        object_type=object_type, domain=domain,
        estimated_value=value, currency=currency,
        published_at=now - timedelta(days=1),
        closing_at=now + timedelta(days=days_to_close),
    )


def require_class(tender, category=None, min_class=2, or_better=True,
                  source_text="Tenderers shall hold class A2 or better."):
    category = category or make_category()
    return TenderClassRequirement.objects.create(
        tender=tender, scheme=category.scheme, category=category,
        min_financial_class=min_class, or_better=or_better, source_text=source_text,
    )


def hold_class(org, category=None, financial_class=1, valid_from=None, valid_to=None,
               verified=True, certificate=None):
    category = category or make_category()
    return Classification.objects.create(
        organization=org, scheme=category.scheme, category=category,
        financial_class=financial_class, valid_from=valid_from, valid_to=valid_to,
        certificate=certificate,
        verified_against_register_at=timezone.now() if verified else None,
    )


def make_document(org, spec=None, *, expires_on=None, issued_on=None,
                  extracted=False, verified=False, title="Certificate"):
    return Document.objects.create(
        organization=org, spec=spec, title=title, file="vault/test.pdf",
        expires_on=expires_on, issued_on=issued_on,
        extracted=extracted, verified_by_human=verified,
    )


def make_cap(scheme=None, financial_class=1, amount="500000", currency="GHS",
             is_stale=False, effective_from=date(2004, 1, 1), category=None):
    scheme = scheme or make_scheme()
    return FinancialClassCap.objects.create(
        scheme=scheme, category=category, financial_class=financial_class,
        amount=amount, currency=currency, is_stale=is_stale,
        effective_from=effective_from,
    )


def complete_five_years(org, reference_year, count=5):
    """Give an org a clean five-year record so other findings can be isolated."""
    docs = []
    for offset in range(1, count + 1):
        year = reference_year - offset
        accounts = make_document(org, title=f"Audited accounts {year}", verified=True)
        docs.append(
            FinancialYear.objects.create(
                organization=org, year=year, turnover="1000000",
                audited_accounts=accounts,
            )
        )
    return docs
