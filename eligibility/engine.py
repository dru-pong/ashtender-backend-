"""
The eligibility engine.

The product is this file.  Everything else exists to feed it or to show what it
decided.

Three invariants, all load-bearing:

*   **The verdict is never a boolean.**  PASS / FAIL / UNKNOWN / WAIVABLE, and
    UNKNOWN is the important one: it is how the system says "a person needs to
    look at this" instead of quietly discarding a tender the firm could have won.
*   **The LLM extracts, Python decides.**  Nothing in this module imports the
    gateway.  A model may tell us a notice demands a Labour Certificate; only
    the code below decides whether that requirement is met.
*   **Every finding carries its evidence.**  The tender sentence that imposed
    the requirement, and the document that answers it.  A refusal that cannot
    be explained with a quote and a file does not get emitted.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from catalog.models import FinancialClassCap
from core.enums import RequirementTier, TIER_PRECEDENCE, Verdict
from eligibility.models import EligibilityCheck, RequirementFinding
from eligibility.rules import (
    ENGINE_VERSION,
    Outcome,
    cap_outcome,
    class_is_satisfied,
    document_validity,
    financial_years_outcome,
    prep_time_outcome,
)
from tenders.models import DebarmentEntry
from vault.models import Classification, Document, FinancialYear, PastProject

logger = logging.getLogger(__name__)

Code = RequirementFinding.Code

#: FAIL beats UNKNOWN beats WAIVABLE beats PASS when rolling findings up.
_VERDICT_RANK = {Verdict.PASS: 0, Verdict.WAIVABLE: 1, Verdict.UNKNOWN: 2, Verdict.FAIL: 3}


def _worst(verdicts) -> str:
    return max(verdicts, key=lambda v: _VERDICT_RANK.get(v, 0), default=Verdict.PASS)


def normalise_company_name(name: str) -> str:
    """
    Squash the variants that make register matching fail.

    "Shamma Rohi Company Limited", "Shamma Rohi Ltd." and "SHAMMA ROHI LIMITED"
    are one firm; a register lookup that misses because of a suffix is worse
    than useless, because it reports a valid classification as absent.
    """
    if not name:
        return ""
    cleaned = name.upper()
    for token in (".", ",", "(", ")", "-", "'", '"'):
        cleaned = cleaned.replace(token, " ")
    words = [w for w in cleaned.split() if w not in
             {"LIMITED", "LTD", "COMPANY", "CO", "ENTERPRISE", "ENTERPRISES",
              "AND", "&", "THE"}]
    return " ".join(words).strip()


# --- requirement resolution --------------------------------------------
def resolve_requirements(tender):
    """
    Which specs apply to this tender, with precedence resolved.

    UNIVERSAL (statutory pack everyone needs) is widened by DOMAIN rules and
    then overridden by anything the notice states explicitly.  Precedence
    matters because a notice that waives a document must win over our
    assumption that it is always required.
    """
    from catalog.models import DomainRequirementRule, RequirementSpec

    resolved: dict[int, dict] = {}

    def offer(spec, tier, is_mandatory, source_text="", requirement=None):
        existing = resolved.get(spec.id)
        if existing and TIER_PRECEDENCE[existing["tier"]] >= TIER_PRECEDENCE[tier]:
            return
        resolved[spec.id] = {
            "spec": spec,
            "tier": tier,
            "is_mandatory": is_mandatory,
            "source_text": source_text,
            "requirement": requirement,
        }

    universal = RequirementSpec.objects.filter(
        domainrequirementrule__tier=RequirementTier.UNIVERSAL
    ).distinct()
    for spec in universal:
        offer(spec, RequirementTier.UNIVERSAL, True)

    if tender.domain_id:
        rules = (
            DomainRequirementRule.objects.filter(domain_id=tender.domain_id)
            .select_related("spec", "spec__authority")
        )
        for rule in rules:
            offer(rule.spec, rule.tier, rule.is_mandatory)

    explicit = tender.requirements.select_related("spec", "spec__authority").all()
    for requirement in explicit:
        offer(
            requirement.spec,
            RequirementTier.TENDER_EXPLICIT,
            requirement.is_mandatory,
            requirement.source_text,
            requirement,
        )

    return list(resolved.values())


def best_document(organization_id: int, spec) -> Document | None:
    """
    The strongest document on file for a spec.

    Human-verified beats machine-extracted, then latest expiry, then latest
    issue date.  Ordering rather than filtering, because an unverified document
    is still worth reporting as UNKNOWN -- it tells the tenant they have the
    paper and need to confirm a date, which is a five-minute job, rather than
    telling them the document is missing, which sends them to the GRA.
    """
    return (
        Document.objects.for_org(organization_id)
        .filter(spec=spec, superseded_by__isnull=True)
        .order_by("-verified_by_human", "-expires_on", "-issued_on", "-created_at")
        .first()
    )


# --- layer 1: deterministic screens -------------------------------------
def _classification_findings(organization, tender, closing_date: date):
    """
    Compare held classifications against those the tender demands.

    The three-way split matters:

    *   Nothing held in that category  -> FAIL.  Genuinely cannot bid.
    *   Held, but the class is too low -> FAIL.
    *   Held at the right class, but the validity window has passed, or it was
        never checked against the ministry's register -> UNKNOWN.

    That last case is the common one and the reason it is not a FAIL: the
    published registers lag reality by months. A firm can hold a current
    certificate in hand while the last list on the ministry's website still
    shows the previous cycle. Reporting that as a definite bar would tell a
    tenant they cannot bid work they are perfectly entitled to bid.
    """
    findings = []
    required = tender.required_classes.select_related("scheme", "category").all()
    if not required:
        return findings

    held = list(
        Classification.objects.for_org(organization.pk).select_related("scheme", "category")
    )

    for requirement in required:
        matches = [
            classification
            for classification in held
            if classification.scheme_id == requirement.scheme_id
            and classification.category_id == requirement.category_id
        ]
        label = f"{requirement.category.letter}{requirement.min_financial_class}"

        if not matches:
            letters_held = ", ".join(sorted({str(c) for c in held})) or "none on file"
            findings.append(
                dict(
                    code=Code.CLASSIFICATION,
                    verdict=Verdict.FAIL,
                    reason=(
                        f"This tender requires {requirement.scheme.code} class {label}. "
                        f"No {requirement.category.letter} classification is on file "
                        f"(held: {letters_held})."
                    ),
                    source_text=requirement.source_text,
                    remedy=(
                        f"Apply to {requirement.scheme.get_code_display()} for a "
                        f"category {requirement.category.letter} classification."
                    ),
                )
            )
            continue

        qualifying = [
            classification
            for classification in matches
            if class_is_satisfied(
                classification.financial_class,
                requirement.min_financial_class,
                requirement.or_better,
            )
        ]
        if not qualifying:
            best = min(matches, key=lambda c: c.financial_class)
            findings.append(
                dict(
                    code=Code.CLASSIFICATION,
                    verdict=Verdict.FAIL,
                    reason=(
                        f"This tender requires {label} or better; the firm holds "
                        f"{best.category.letter}{best.financial_class}. "
                        "Class 1 is the highest capacity."
                    ),
                    source_text=requirement.source_text,
                    satisfied_by_classification=best,
                    remedy="Upgrade the financial class, or bid in joint venture.",
                )
            )
            continue

        chosen = min(qualifying, key=lambda c: c.financial_class)
        currency = chosen.is_current_on(closing_date)

        if currency is False:
            findings.append(
                dict(
                    code=Code.CLASSIFICATION_LAPSED,
                    verdict=Verdict.UNKNOWN,
                    reason=(
                        f"The firm holds {chosen.category.letter}{chosen.financial_class}, "
                        f"which satisfies {label}, but the validity on record ended "
                        f"{chosen.valid_to:%d %b %Y}. Published ministry lists lag, so "
                        "this is not treated as a bar."
                    ),
                    source_text=requirement.source_text,
                    satisfied_by_classification=chosen,
                    satisfied_by=chosen.certificate,
                    remedy=(
                        "Confirm with the classifying ministry whether a current "
                        "certificate has been issued, and upload it."
                    ),
                )
            )
        elif currency is None:
            findings.append(
                dict(
                    code=Code.CLASSIFICATION_LAPSED,
                    verdict=Verdict.UNKNOWN,
                    reason=(
                        f"The firm holds {chosen.category.letter}{chosen.financial_class}, "
                        f"which satisfies {label}, but no validity window has been "
                        "recorded, so currency cannot be checked."
                    ),
                    source_text=requirement.source_text,
                    satisfied_by_classification=chosen,
                    remedy="Record the valid-from and valid-to dates from the certificate.",
                )
            )
        elif chosen.verified_against_register_at is None:
            findings.append(
                dict(
                    code=Code.CLASSIFICATION_UNVERIFIED,
                    verdict=Verdict.UNKNOWN,
                    reason=(
                        f"{chosen.category.letter}{chosen.financial_class} satisfies "
                        f"{label} on the dates recorded, but it has not been checked "
                        "against the ministry's published register."
                    ),
                    source_text=requirement.source_text,
                    satisfied_by_classification=chosen,
                    remedy="Run register verification, or confirm by phone.",
                )
            )
        else:
            findings.append(
                dict(
                    code=Code.CLASSIFICATION,
                    verdict=Verdict.PASS,
                    reason=(
                        f"{chosen.category.letter}{chosen.financial_class} satisfies "
                        f"{label}, register-verified "
                        f"{chosen.verified_against_register_at:%d %b %Y}."
                    ),
                    source_text=requirement.source_text,
                    satisfied_by_classification=chosen,
                    satisfied_by=chosen.certificate,
                )
            )
    return findings


def _value_cap_findings(organization, tender, closing_date: date):
    """Check the tender value against the caps for every class the firm holds."""
    if tender.estimated_value is None:
        return []

    held = list(
        Classification.objects.for_org(organization.pk).select_related("scheme", "category")
    )
    if not held:
        return []

    findings = []
    boundary = settings.ELIGIBILITY_CAP_BOUNDARY_FRACTION
    relevant = tender.required_classes.values_list("scheme_id", "category_id")
    relevant_pairs = set(relevant)

    for classification in held:
        if relevant_pairs and (classification.scheme_id, classification.category_id) not in relevant_pairs:
            continue
        cap = (
            FinancialClassCap.objects.filter(
                scheme_id=classification.scheme_id,
                financial_class=classification.financial_class,
                effective_from__lte=closing_date,
            )
            .filter(
                models_q_category(classification.category_id)
            )
            .order_by("-effective_from")
            .first()
        )
        if cap is None:
            continue

        value = _to_cap_currency(tender, cap, closing_date)
        if value is None:
            findings.append(
                dict(
                    code=Code.VALUE_CAP,
                    verdict=Verdict.UNKNOWN,
                    reason=(
                        f"The tender value is in {tender.currency} but the ceiling for "
                        f"{classification} is recorded in {cap.currency}, and no exchange "
                        f"rate is on file for {closing_date:%d %b %Y}."
                    ),
                    remedy="Load the Bank of Ghana rate for the publication date.",
                )
            )
            continue

        outcome = cap_outcome(
            value, cap.amount, boundary, cap.is_stale, cap_label=str(classification)
        )
        code = Code.VALUE_CAP_BOUNDARY if outcome.verdict == Verdict.UNKNOWN else Code.VALUE_CAP
        findings.append(
            dict(
                code=code,
                verdict=outcome.verdict,
                reason=outcome.reason,
                remedy=outcome.remedy,
                satisfied_by_classification=classification,
            )
        )
    return findings


def models_q_category(category_id):
    """Cap rows with a null category apply to every category in the scheme."""
    from django.db.models import Q

    return Q(category_id=category_id) | Q(category__isnull=True)


def _to_cap_currency(tender, cap, on_date) -> Decimal | None:
    """
    Convert a tender value into the cap's currency.

    Convention, fixed so verdicts are reproducible: use the rate effective on
    or before the date being assessed, never today's rate.  A verdict must not
    flip because the cedi moved after it was computed.
    """
    from catalog.models import FxRate

    if tender.currency == cap.currency:
        return tender.estimated_value

    pair = f"{cap.currency}{tender.currency}"
    rate = (
        FxRate.objects.filter(currency_pair=pair, as_of__lte=on_date)
        .order_by("-as_of")
        .first()
    )
    if rate is None or not rate.rate:
        return None
    return (Decimal(tender.estimated_value) / Decimal(rate.rate)).quantize(Decimal("0.01"))


def _debarment_findings(organization, profile, closing_date: date):
    """
    Check the firm against public exclusion lists.

    Matching is on a normalised name, which is a real limitation: it can miss a
    debarred firm trading under a variant, and it can flag an unrelated firm
    with a similar name.  So a hit is reported with the list and the dates
    attached, and it is the one place where FAIL is emitted on a name match --
    because bidding while debarred is worse than a false alarm.
    """
    names = {normalise_company_name(organization.name)}
    if profile:
        names.add(normalise_company_name(profile.legal_name))
        if profile.trading_name:
            names.add(normalise_company_name(profile.trading_name))
    names.discard("")
    if not names:
        return []

    findings = []
    for entry in DebarmentEntry.objects.filter(company_name_normalised__in=names):
        if not entry.is_active_on(closing_date):
            continue
        window = ""
        if entry.from_date or entry.to_date:
            window = (
                f" ({entry.from_date or 'unknown start'} to "
                f"{entry.to_date or 'no stated end'})"
            )
        findings.append(
            dict(
                code=Code.DEBARMENT,
                verdict=Verdict.FAIL,
                reason=(
                    f"A firm matching this name appears on the "
                    f"{entry.get_authority_display()} exclusion list{window}. "
                    "Bidding while debarred is itself an offence."
                ),
                source_text=entry.reason,
                remedy=(
                    "If this is a different company with a similar name, mark it "
                    "as such so future checks skip it."
                ),
            )
        )
    return findings


# --- layer 2: document requirements ------------------------------------
def _document_findings(organization, tender, closing_date: date):
    findings = []
    for resolved in resolve_requirements(tender):
        spec = resolved["spec"]
        source_text = resolved["source_text"]
        mandatory = resolved["is_mandatory"]

        if spec.is_conditional:
            applicable = _conditional_applies(spec, organization)
            if applicable is False:
                findings.append(
                    dict(
                        code=Code.DOCUMENT, spec=spec, verdict=Verdict.PASS,
                        is_mandatory=mandatory, source_text=source_text,
                        reason=f"{spec.name} does not apply to this firm. {spec.condition_note}",
                    )
                )
                continue

        doc = best_document(organization.pk, spec)
        if doc is None:
            findings.append(
                dict(
                    code=Code.DOCUMENT, spec=spec,
                    verdict=Verdict.FAIL if mandatory else Verdict.WAIVABLE,
                    is_mandatory=mandatory, source_text=source_text,
                    reason=f"{spec.name} is not in the vault.",
                    remedy=f"Obtain it from {spec.authority.name} and upload it.",
                )
            )
            continue

        outcome = document_validity(doc, spec, closing_date)
        verdict = outcome.verdict

        # An unverified extraction is good enough to screen a tender in, but
        # not good enough to assemble a bid on.  Downgrading PASS to UNKNOWN
        # here is what stops a hallucinated expiry date reaching a submission.
        if verdict == Verdict.PASS and doc.extracted and not doc.verified_by_human:
            findings.append(
                dict(
                    code=Code.DOCUMENT, spec=spec, verdict=Verdict.UNKNOWN,
                    is_mandatory=mandatory, source_text=source_text,
                    satisfied_by=doc,
                    reason=(
                        f"{outcome.reason} The dates were read automatically and "
                        "have not been confirmed by a person."
                    ),
                    remedy="Confirm the dates against the page crop.",
                )
            )
            continue

        if verdict == Verdict.FAIL and not mandatory:
            verdict = Verdict.WAIVABLE

        findings.append(
            dict(
                code=Code.DOCUMENT, spec=spec, verdict=verdict,
                is_mandatory=mandatory, source_text=source_text,
                satisfied_by=doc, reason=outcome.reason, remedy=outcome.remedy,
            )
        )
    return findings


def _conditional_applies(spec, organization) -> bool | None:
    """
    Whether an "if applicable" requirement bites.

    Only VAT is wired up, because it is the only conditional in the statutory
    pack whose condition is recorded on the profile.  Anything else returns
    None, which keeps the requirement in play rather than silently excusing it.
    """
    profile = getattr(organization, "company_profile", None)
    if profile is None:
        return None
    if spec.code in ("vat-registration", "vat_registration"):
        return bool(profile.vat_registered)
    return None


# --- layer 3: five-year completeness -----------------------------------
def _five_year_findings(organization, tender, closing_date: date):
    findings = []
    required_years = settings.ELIGIBILITY_FINANCIAL_YEAR_LOOKBACK

    years = list(FinancialYear.objects.for_org(organization.pk))
    outcome = financial_years_outcome(years, required_years, closing_date.year)
    findings.append(
        dict(
            code=Code.FINANCIAL_YEARS, verdict=outcome.verdict,
            reason=outcome.reason, remedy=outcome.remedy,
        )
    )

    lookback = settings.ELIGIBILITY_PAST_PROJECT_LOOKBACK_YEARS
    earliest = date(closing_date.year - lookback, closing_date.month, 1)
    discipline_ids = list(
        tender.tender_disciplines.values_list("discipline_id", flat=True)
    )

    projects = PastProject.objects.for_org(organization.pk).filter(
        completed_on__gte=earliest
    )
    if discipline_ids:
        matching = projects.filter(disciplines__id__in=discipline_ids).distinct()
        if matching.exists():
            findings.append(
                dict(
                    code=Code.SIMILAR_WORKS, verdict=Verdict.PASS,
                    reason=(
                        f"{matching.count()} similar project(s) completed since "
                        f"{earliest:%b %Y} match this tender's disciplines."
                    ),
                )
            )
        else:
            findings.append(
                dict(
                    code=Code.SIMILAR_WORKS, verdict=Verdict.FAIL,
                    reason=(
                        "No completed project in the last "
                        f"{lookback} years matches this tender's disciplines."
                    ),
                    remedy=(
                        "Add the relevant past projects with their completion "
                        "certificates, or reconsider bidding."
                    ),
                )
            )
    else:
        findings.append(
            dict(
                code=Code.SIMILAR_WORKS, verdict=Verdict.UNKNOWN,
                reason=(
                    "This tender has not been tagged with disciplines, so "
                    "similar-works experience cannot be matched."
                ),
                remedy="Tag the tender's disciplines, then re-run the check.",
            )
        )
    return findings


# --- orchestration ------------------------------------------------------
def _inputs_digest(organization, tender) -> str:
    """
    Fingerprint of everything the verdict depended on.

    Cheap change detection: if this is unchanged, last night's precomputed
    verdict still holds and does not need recomputing.
    """
    document_stamps = list(
        Document.objects.for_org(organization.pk)
        .order_by("pk")
        .values_list("pk", "updated_at", "verified_by_human")
    )
    classification_stamps = list(
        Classification.objects.for_org(organization.pk)
        .order_by("pk")
        .values_list("pk", "updated_at")
    )
    blob = json.dumps(
        {
            "engine": ENGINE_VERSION,
            "tender": [tender.pk, str(tender.updated_at)],
            "documents": document_stamps,
            "classifications": classification_stamps,
        },
        default=str, sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


@transaction.atomic
def evaluate(organization, tender, *, now=None, persist: bool = True) -> EligibilityCheck:
    """
    Evaluate one tender for one tenant and return an immutable check.

    Screening short-circuits: a tender for goods offered to a works-only firm,
    or one closing tomorrow, produces SCREENED_OUT with one finding rather than
    a full document audit.  That is not an optimisation, it is what keeps the
    daily digest readable.
    """
    now = now or timezone.now()
    closing_date = (tender.closing_at or now).date()
    profile = getattr(organization, "company_profile", None)

    check = EligibilityCheck(
        organization=organization, tender=tender, computed_at=now,
        verdict=Verdict.UNKNOWN, engine_version=ENGINE_VERSION,
        assessed_against_closing_at=tender.closing_at,
        inputs_digest=_inputs_digest(organization, tender),
    )

    finding_dicts: list[dict] = []
    screened_out_reason = ""

    # --- layer 1 --------------------------------------------------------
    if profile and profile.object_types and tender.object_type not in profile.object_types:
        screened_out_reason = "object_type"
        finding_dicts.append(
            dict(
                code=Code.OBJECT_TYPE, verdict=Verdict.FAIL,
                reason=(
                    f"This is a {tender.get_object_type_display()} tender; this "
                    f"firm bids {', '.join(profile.object_types)}."
                ),
            )
        )
    else:
        prep = prep_time_outcome(
            tender.closing_at, now, profile.min_prep_days if profile else 0
        )
        if prep.verdict == Verdict.FAIL:
            screened_out_reason = "late"
            finding_dicts.append(
                dict(code=Code.LATE, verdict=Verdict.FAIL, reason=prep.reason,
                     remedy=prep.remedy)
            )
        else:
            finding_dicts.extend(_debarment_findings(organization, profile, closing_date))
            hard_stop = any(f["verdict"] == Verdict.FAIL for f in finding_dicts)
            if hard_stop:
                screened_out_reason = "debarred"
            else:
                finding_dicts.extend(
                    _classification_findings(organization, tender, closing_date)
                )
                finding_dicts.extend(_value_cap_findings(organization, tender, closing_date))
                # --- layer 2 & 3 ---------------------------------------
                finding_dicts.extend(_document_findings(organization, tender, closing_date))
                finding_dicts.extend(_five_year_findings(organization, tender, closing_date))

    check.verdict = _worst([f["verdict"] for f in finding_dicts])
    check.screened_out_reason = screened_out_reason
    check.summary = _summarise(check.verdict, finding_dicts, screened_out_reason)

    if not persist:
        check.unsaved_findings = finding_dicts
        return check

    check.save()
    RequirementFinding.objects.bulk_create(
        [
            RequirementFinding(
                organization=organization, eligibility_check=check,
                code=f["code"], spec=f.get("spec"), verdict=f["verdict"],
                is_mandatory=f.get("is_mandatory", True), reason=f["reason"],
                source_text=f.get("source_text", "") or "",
                satisfied_by=f.get("satisfied_by"),
                satisfied_by_classification=f.get("satisfied_by_classification"),
                confidence=f.get("confidence"), remedy=f.get("remedy", "") or "",
            )
            for f in finding_dicts
        ]
    )
    return check


def _summarise(verdict: str, findings, screened_out_reason: str) -> str:
    if screened_out_reason:
        return findings[0]["reason"] if findings else f"Screened out: {screened_out_reason}."
    fails = [f for f in findings if f["verdict"] == Verdict.FAIL]
    unknowns = [f for f in findings if f["verdict"] == Verdict.UNKNOWN]
    if verdict == Verdict.PASS:
        return f"All {len(findings)} checks passed."
    parts = []
    if fails:
        parts.append(f"{len(fails)} blocking: " + "; ".join(f["reason"] for f in fails[:3]))
    if unknowns:
        parts.append(
            f"{len(unknowns)} need a human: " + "; ".join(f["reason"] for f in unknowns[:3])
        )
    return " | ".join(parts)
