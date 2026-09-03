"""
Pure rule functions: no database, no LLM, no clock of their own.

Kept separate from the engine so the arithmetic that decides whether a firm can
bid is unit-testable without fixtures, and so it is obvious by inspection that
nothing here consults a language model.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from core.enums import ValidityMode, Verdict

#: Bumped whenever the logic below changes meaning.  Stored on every check.
ENGINE_VERSION = "0.1.0"


@dataclass(frozen=True)
class Outcome:
    """A verdict plus the words to explain it."""

    verdict: str
    reason: str
    remedy: str = ""
    source_text: str = ""

    @property
    def is_blocking(self) -> bool:
        return self.verdict in (Verdict.FAIL, Verdict.UNKNOWN)


def document_validity(doc, spec, closing_date: date) -> Outcome:
    """
    Is this document still good on closing day?

    The four validity modes exist because Ghanaian tender documents mix them
    freely: a tax clearance carries a printed expiry, a bank reference is only
    accepted if recently issued, a certificate of incorporation never expires,
    and a PPA registration is renewed annually.  Collapsing them into one
    "expires_on" field would make two of the four unrepresentable.
    """
    mode = spec.validity_mode

    if mode == ValidityMode.PERPETUAL:
        return Outcome(Verdict.PASS, f"{spec.name} does not expire.")

    if mode == ValidityMode.PRINTED_EXPIRY:
        if doc.expires_on is None:
            return Outcome(
                Verdict.UNKNOWN,
                f"{spec.name} is on file but no expiry date has been recorded.",
                remedy="Confirm the expiry date printed on the certificate.",
            )
        if doc.expires_on >= closing_date:
            return Outcome(
                Verdict.PASS,
                f"{spec.name} is valid until {doc.expires_on:%d %b %Y}.",
            )
        return Outcome(
            Verdict.FAIL,
            f"{spec.name} expired on {doc.expires_on:%d %b %Y}, before this "
            f"tender closes on {closing_date:%d %b %Y}.",
            remedy=f"Renew with {spec.authority.name} before closing.",
        )

    if mode == ValidityMode.ISSUE_RECENCY:
        if doc.issued_on is None:
            return Outcome(
                Verdict.UNKNOWN,
                f"{spec.name} is on file but its issue date has not been recorded.",
                remedy="Confirm the issue date on the document.",
            )
        months = spec.recency_months or 0
        earliest = closing_date - timedelta(days=int(months * 30.44))
        if doc.issued_on >= earliest:
            return Outcome(
                Verdict.PASS,
                f"{spec.name} was issued {doc.issued_on:%d %b %Y}, within the "
                f"required {months} months.",
            )
        return Outcome(
            Verdict.FAIL,
            f"{spec.name} was issued {doc.issued_on:%d %b %Y}; this tender needs "
            f"one issued within {months} months of closing.",
            remedy=f"Request a fresh {spec.doc_type} from {spec.authority.name}.",
        )

    if mode == ValidityMode.ANNUAL_RENEWAL:
        if doc.issued_on is None:
            return Outcome(
                Verdict.UNKNOWN,
                f"{spec.name} is on file but its issue date has not been recorded.",
                remedy="Confirm the issue date, then the renewal year can be checked.",
            )
        if doc.issued_on.year >= closing_date.year:
            return Outcome(
                Verdict.PASS,
                f"{spec.name} was renewed for {doc.issued_on.year}.",
            )
        return Outcome(
            Verdict.FAIL,
            f"{spec.name} was last renewed in {doc.issued_on.year}; "
            f"{closing_date.year} renewal is not on file.",
            remedy=f"Pay the annual renewal to {spec.authority.name}.",
        )

    return Outcome(
        Verdict.UNKNOWN, f"{spec.name} has an unrecognised validity mode ({mode})."
    )


def class_is_satisfied(held_class: int, required_class: int, or_better: bool = True) -> bool:
    """
    Lower class number means higher capacity.

    Written as its own function purely so the inversion is tested in one place;
    getting it backwards would screen a class-1 firm out of every class-3
    tender, which is the most expensive possible bug for a tenant.
    """
    held, required = int(held_class), int(required_class)
    return held <= required if or_better else held == required


def cap_outcome(tender_value: Decimal | None, cap_amount: Decimal | None,
                boundary_fraction: float, cap_is_stale: bool,
                cap_label: str = "") -> Outcome:
    """
    Compare a tender value against a financial-class cap.

    Three deliberate softenings, all because the published cap figures are not
    currently confirmed against a primary source:

    *   No value on the tender yields UNKNOWN, not PASS.
    *   A value within ``boundary_fraction`` of the cap yields UNKNOWN, because
        the cap itself may be wrong by more than that margin.
    *   A *stale* cap can never produce FAIL on its own.  The worst it can do
        is send the tender to a human.
    """
    if cap_amount is None:
        return Outcome(Verdict.PASS, "No value ceiling applies to this class.")
    if tender_value is None:
        return Outcome(
            Verdict.UNKNOWN,
            "The tender does not state a value, so it cannot be compared with "
            f"the {cap_label or 'class'} ceiling.",
            remedy="Ask the entity for the estimated value, or read it off the BoQ.",
        )

    value = Decimal(tender_value)
    cap = Decimal(cap_amount)
    band = cap * Decimal(str(boundary_fraction))

    if value <= cap - band:
        return Outcome(
            Verdict.PASS,
            f"Value {value:,.0f} is within the {cap_label or 'class'} ceiling "
            f"of {cap:,.0f}.",
        )
    if value <= cap + band:
        return Outcome(
            Verdict.UNKNOWN,
            f"Value {value:,.0f} sits within {boundary_fraction:.0%} of the "
            f"{cap_label or 'class'} ceiling of {cap:,.0f}. The published "
            "ceiling figures are not currently confirmed, so this needs a human.",
            remedy="Confirm the current ceiling with the classifying ministry.",
        )
    if cap_is_stale:
        return Outcome(
            Verdict.UNKNOWN,
            f"Value {value:,.0f} exceeds the {cap_label or 'class'} ceiling on "
            f"record ({cap:,.0f}), but that figure is unconfirmed, so this is "
            "not treated as a definite bar.",
            remedy="Confirm the current ceiling with the classifying ministry.",
        )
    return Outcome(
        Verdict.FAIL,
        f"Value {value:,.0f} exceeds the {cap_label or 'class'} ceiling of {cap:,.0f}.",
        remedy="Upgrade the financial class, or bid as part of a joint venture.",
    )


def prep_time_outcome(closing_at, now, min_prep_days: int) -> Outcome:
    """Windows run 7-10 days; below a firm's own floor this is noise, not a lead."""
    if closing_at is None:
        return Outcome(Verdict.UNKNOWN, "No closing date recorded for this tender.")
    days = (closing_at - now).days
    if days < 0:
        return Outcome(Verdict.FAIL, f"This tender closed {abs(days)} days ago.")
    if days < min_prep_days:
        return Outcome(
            Verdict.FAIL,
            f"Only {days} days to closing; this firm's minimum preparation "
            f"window is {min_prep_days} days.",
            remedy="Override manually if the documents are already assembled.",
        )
    return Outcome(Verdict.PASS, f"{days} days to closing.")


def financial_years_outcome(years, required: int, reference_year: int) -> Outcome:
    """
    Section IV asks for five years of audited accounts and turnover.

    A gap is reported by naming the missing years, because "incomplete" is not
    actionable and "FY2022 and FY2021 audited accounts are missing" is.
    """
    wanted = [reference_year - offset for offset in range(1, required + 1)]
    complete = {fy.year for fy in years if fy.is_complete}
    missing = [year for year in wanted if year not in complete]
    if not missing:
        return Outcome(
            Verdict.PASS, f"Audited accounts and turnover on file for {min(wanted)}-{max(wanted)}."
        )
    partial = {fy.year for fy in years} & set(missing)
    detail = ", ".join(str(year) for year in missing)
    if partial:
        detail += (
            f" (records exist for {', '.join(str(y) for y in sorted(partial))} "
            "but without audited accounts)"
        )
    return Outcome(
        Verdict.FAIL,
        f"The {required}-year financial record is incomplete. Missing: {detail}.",
        remedy="Upload the audited accounts for the missing years.",
    )
