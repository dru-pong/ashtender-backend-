"""
OCDS ingestion: mine the GHANEPS / OCDS award corpus.

Spec section 11 phase 3.  This is the highest-leverage acquisition task because
one pass unblocks three things at once:

*   real tender titles for discipline mining (``catalog.Discipline``),
*   the competitor graph and incumbency signal (``AwardHistory``), and
*   real notice text to test requirement extraction against, instead of
    synthetic fixtures.

Two deliberate shapes, both matching conventions already in the tree:

*   **Parsing is pure.**  ``parse_release`` turns one OCDS release dict into a
    plain ``ParsedRelease`` dataclass with no database and no clock of its own.
    ``ingest_releases`` is the only part that writes rows, so the parse is
    unit-testable without fixtures the way ``catalog.classification`` is.
*   **Winner names are normalised through the same function register matching
    uses** (``eligibility.engine.normalise_company_name``), so the competitor
    graph collapses "Shamma Rohi Limited" / "SHAMMA ROHI COMPANY LTD" the way
    the register oracle already does.  A competitor graph that treats suffix
    variants as different firms is a graph of noise.

An OCDS release is a JSON object.  The corpus is a stream of them, either as a
``{"releases": [...]}` package or newline-delimited (one release per line).
Both shapes are accepted; nothing here fetches -- the caller hands in already
downloaded bytes or an open file, so this module has no network dependency and
no allowlist to satisfy.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from core.enums import Channel, Method, ObjectType

#: OCDS procurementMethod -> our Method enum.  OCDS uses a small controlled
#: vocabulary (open / selective / limited / direct); the Ghanaian method names
#: arrive in procurementMethodDetails as free text, so both are consulted.
_METHOD_BY_OCDS = {
    "open": Method.NCT,
    "selective": Method.RESTRICTED,
    "limited": Method.SINGLE_SOURCE,
    "direct": Method.SINGLE_SOURCE,
}
_METHOD_BY_DETAIL = {
    "national competitive": Method.NCT,
    "international competitive": Method.ICT,
    "request for quotation": Method.RFQ,
    "request for quotations": Method.RFQ,
    "restricted": Method.RESTRICTED,
    "single source": Method.SINGLE_SOURCE,
    "single-source": Method.SINGLE_SOURCE,
    "framework": Method.FRAMEWORK,
    "two stage": Method.TWO_STAGE,
    "two-stage": Method.TWO_STAGE,
}

#: OCDS mainProcurementCategory -> ObjectType.  OCDS only distinguishes
#: goods / works / services; the finer split (consultancy vs not) is not in the
#: category field, so services maps to the broader NON_CONSULTANCY and a title
#: parse can refine it later.
_OBJECT_BY_CATEGORY = {
    "goods": ObjectType.GOODS,
    "works": ObjectType.WORKS,
    "services": ObjectType.NON_CONSULTANCY,
    "consultingServices": ObjectType.CONSULTANCY,
}


@dataclass
class ParsedAward:
    winner_name: str
    winner_name_normalised: str = ""
    value: Decimal | None = None
    currency: str = "GHS"
    award_date: date | None = None
    title: str = ""


@dataclass
class ParsedRelease:
    """
    One OCDS release, flattened to the fields AshTender stores.

    ``is_usable`` is false for a release we cannot key on -- no ocid and no
    buyer name -- because storing it would create a row nothing can dedup
    against or attribute.
    """

    ocid: str = ""
    buyer_name: str = ""
    buyer_ocds_party_id: str = ""
    reference_number: str = ""
    title: str = ""
    description: str = ""
    object_type: str = ""
    method: str = ""
    channel: str = Channel.GOV_GHANEPS
    estimated_value: Decimal | None = None
    currency: str = "GHS"
    published_at: datetime | None = None
    closing_at: datetime | None = None
    is_cancelled: bool = False
    awards: list[ParsedAward] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        return bool(self.ocid) and bool(self.buyer_name)


# --- pure parsing -------------------------------------------------------
def _to_decimal(raw) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_datetime(raw):
    """Parse an OCDS ISO-8601 timestamp, tolerating a trailing Z and date-only."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt)
        except ValueError:
            continue
    return None


def _map_method(tender_block: dict) -> str:
    detail = (tender_block.get("procurementMethodDetails") or "").strip().lower()
    for needle, method in _METHOD_BY_DETAIL.items():
        if needle in detail:
            return method
    ocds_method = (tender_block.get("procurementMethod") or "").strip().lower()
    return _METHOD_BY_OCDS.get(ocds_method, "")


def _map_object_type(tender_block: dict) -> str:
    category = (tender_block.get("mainProcurementCategory") or "").strip()
    return _OBJECT_BY_CATEGORY.get(category, "")


def parse_release(release: dict) -> ParsedRelease:
    """
    Flatten one OCDS release dict.  No database, no network, no clock.

    Robust to missing blocks: an award-only release (no ``tender`` block) still
    yields its awards, and a planning release with no awards still yields its
    buyer and title.  Whatever is absent stays empty rather than guessed.
    """
    from eligibility.engine import normalise_company_name

    if not isinstance(release, dict):
        return ParsedRelease()

    parsed = ParsedRelease(ocid=str(release.get("ocid") or "").strip())

    buyer = release.get("buyer") or {}
    parsed.buyer_name = str(buyer.get("name") or "").strip()
    parsed.buyer_ocds_party_id = str(buyer.get("id") or "").strip()

    # A buyer named only in the parties array (buyer block carries just an id).
    if not parsed.buyer_name and parsed.buyer_ocds_party_id:
        for party in release.get("parties") or []:
            if str(party.get("id") or "") == parsed.buyer_ocds_party_id:
                parsed.buyer_name = str(party.get("name") or "").strip()
                break

    tender_block = release.get("tender") or {}
    parsed.title = str(tender_block.get("title") or "").strip()
    parsed.description = str(tender_block.get("description") or "").strip()
    parsed.reference_number = str(
        tender_block.get("id") or release.get("id") or ""
    ).strip()
    parsed.object_type = _map_object_type(tender_block)
    parsed.method = _map_method(tender_block)
    parsed.is_cancelled = (tender_block.get("status") or "").strip().lower() == "cancelled"

    value_block = tender_block.get("value") or {}
    parsed.estimated_value = _to_decimal(value_block.get("amount"))
    parsed.currency = str(value_block.get("currency") or "GHS").strip() or "GHS"

    period = tender_block.get("tenderPeriod") or {}
    parsed.closing_at = _to_datetime(period.get("endDate"))
    parsed.published_at = _to_datetime(
        release.get("date") or (tender_block.get("datePublished"))
    )

    for award in release.get("awards") or []:
        if (award.get("status") or "active").strip().lower() == "cancelled":
            continue
        suppliers = award.get("suppliers") or []
        award_value = award.get("value") or {}
        award_amount = _to_decimal(award_value.get("amount"))
        award_currency = str(award_value.get("currency") or parsed.currency).strip()
        award_date = _to_datetime(award.get("date"))
        award_title = parsed.title or str(award.get("title") or "").strip()
        for supplier in suppliers:
            name = str(supplier.get("name") or "").strip()
            if not name:
                continue
            parsed.awards.append(
                ParsedAward(
                    winner_name=name,
                    winner_name_normalised=normalise_company_name(name),
                    value=award_amount,
                    currency=award_currency or "GHS",
                    award_date=award_date.date() if award_date else None,
                    title=award_title,
                )
            )

    return parsed


def iter_releases(raw: str):
    """
    Yield release dicts from either an OCDS package or NDJSON.

    A package is ``{"releases": [...]}`` (or the record-package variant with
    ``records[].compiledRelease``).  NDJSON is one release JSON object per
    line.  Anything unparseable on a line is skipped rather than aborting the
    whole corpus -- a single malformed release must not lose the rest.
    """
    raw = (raw or "").strip()
    if not raw:
        return
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        loaded = None

    if isinstance(loaded, dict):
        if "releases" in loaded:
            yield from (r for r in loaded["releases"] if isinstance(r, dict))
            return
        if "records" in loaded:
            for record in loaded["records"]:
                compiled = (record or {}).get("compiledRelease")
                if isinstance(compiled, dict):
                    yield compiled
            return
        yield loaded
        return
    if isinstance(loaded, list):
        yield from (r for r in loaded if isinstance(r, dict))
        return

    # NDJSON fallback.
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


# --- ingestion (the only part that writes) ------------------------------
@dataclass
class IngestStats:
    releases_seen: int = 0
    releases_skipped: int = 0
    tenders_created: int = 0
    tenders_updated: int = 0
    entities_created: int = 0
    awards_created: int = 0

    def as_dict(self) -> dict:
        return {
            "releases_seen": self.releases_seen,
            "releases_skipped": self.releases_skipped,
            "tenders_created": self.tenders_created,
            "tenders_updated": self.tenders_updated,
            "entities_created": self.entities_created,
            "awards_created": self.awards_created,
        }


def _entity_slug(name: str, ocds_party_id: str) -> str:
    from django.utils.text import slugify

    base = slugify(name)[:200] or "entity"
    if ocds_party_id:
        suffix = slugify(ocds_party_id)[:40]
        return f"{base}-{suffix}"[:255]
    return base[:255]


def resolve_entity(parsed: ParsedRelease):
    """
    Find or create the ProcuringEntity for a release, keyed on ocds_party_id
    first and name second.

    ``ocds_party_id`` is the durable key: a buyer can be renamed between
    releases but keeps its party id, so matching on the id avoids splitting one
    ministry into two competitor-graph nodes.  A newly created entity records
    the party id so the next release with the same buyer finds it.
    """
    from catalog.models import ProcuringEntity

    created = False
    entity = None
    if parsed.buyer_ocds_party_id:
        entity = ProcuringEntity.objects.filter(
            ocds_party_id=parsed.buyer_ocds_party_id
        ).first()
    if entity is None:
        entity = ProcuringEntity.objects.filter(name__iexact=parsed.buyer_name).first()
    if entity is None:
        entity = ProcuringEntity.objects.create(
            name=parsed.buyer_name[:255],
            slug=_unique_entity_slug(parsed.buyer_name, parsed.buyer_ocds_party_id),
            channel=parsed.channel or Channel.GOV_GHANEPS,
            ocds_party_id=parsed.buyer_ocds_party_id[:128],
        )
        created = True
    elif parsed.buyer_ocds_party_id and not entity.ocds_party_id:
        # Backfill the party id onto an entity first seen without one.
        entity.ocds_party_id = parsed.buyer_ocds_party_id[:128]
        entity.save(update_fields=["ocds_party_id"])
    return entity, created


def _unique_entity_slug(name: str, ocds_party_id: str) -> str:
    from catalog.models import ProcuringEntity

    candidate = _entity_slug(name, ocds_party_id)
    if not ProcuringEntity.objects.filter(slug=candidate).exists():
        return candidate
    n = 2
    while ProcuringEntity.objects.filter(slug=f"{candidate[:250]}-{n}").exists():
        n += 1
    return f"{candidate[:250]}-{n}"


def ingest_release(parsed: ParsedRelease, stats: IngestStats) -> None:
    """
    Write one parsed release: its entity, its tender, and its awards.

    Idempotent on (entity, reference_number) via the model's unique constraint,
    and on ocid where a reference is absent, so re-running the ingester over an
    overlapping corpus updates rather than duplicates.  ``raw_text`` is filled
    from title+description so requirement extraction has text to work on even
    before a notice PDF is attached.
    """
    from tenders.models import AwardHistory, Tender

    stats.releases_seen += 1
    if not parsed.is_usable:
        stats.releases_skipped += 1
        return

    entity, entity_created = resolve_entity(parsed)
    if entity_created:
        stats.entities_created += 1
    if parsed.buyer_ocds_party_id and not entity.ocds_party_id:
        entity.ocds_party_id = parsed.buyer_ocds_party_id[:128]
        entity.save(update_fields=["ocds_party_id"])

    tender = None
    if parsed.reference_number:
        tender = Tender.objects.filter(
            entity=entity, reference_number=parsed.reference_number
        ).first()
    if tender is None and parsed.ocid:
        tender = Tender.objects.filter(ocid=parsed.ocid).first()

    defaults = dict(
        ocid=parsed.ocid[:128],
        title=parsed.title or parsed.reference_number or parsed.ocid,
        description=parsed.description,
        object_type=parsed.object_type or ObjectType.WORKS,
        method=parsed.method or "",
        estimated_value=parsed.estimated_value,
        currency=parsed.currency,
        published_at=parsed.published_at,
        closing_at=parsed.closing_at,
        is_cancelled=parsed.is_cancelled,
        raw_text="\n\n".join(t for t in (parsed.title, parsed.description) if t),
    )

    if tender is None:
        tender = Tender.objects.create(
            entity=entity,
            reference_number=parsed.reference_number[:160],
            **defaults,
        )
        stats.tenders_created += 1
    else:
        for key, value in defaults.items():
            setattr(tender, key, value)
        if not tender.reference_number and parsed.reference_number:
            tender.reference_number = parsed.reference_number[:160]
        tender.save()
        stats.tenders_updated += 1

    for award in parsed.awards:
        _, created = AwardHistory.objects.update_or_create(
            entity=entity,
            ocid=parsed.ocid[:128],
            winner_name=award.winner_name[:255],
            defaults=dict(
                tender=tender,
                winner_name_normalised=award.winner_name_normalised[:255],
                value=award.value,
                currency=award.currency,
                award_date=award.award_date,
                title=award.title,
            ),
        )
        if created:
            stats.awards_created += 1


def ingest_releases(releases, *, stats: IngestStats | None = None) -> IngestStats:
    """Parse and ingest an iterable of raw OCDS release dicts."""
    stats = stats or IngestStats()
    for release in releases:
        ingest_release(parse_release(release), stats)
    return stats
