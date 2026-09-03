"""
Source harvesting: turn a Source row into TenderSighting rows.

Spec section 4.3 and the hard constraint from the brief: **adding a source must
stay a database row plus selector config, never a new Python module.**  If the
first harvester needs bespoke per-source code, the ``Source.config`` JSON shape
is wrong -- and this is where that would show up.

So the strategy classes here are generic.  A ``FeedStrategy`` knows how to walk
a JSON feed using *paths declared in config*; it does not know anything about
any particular ministry.  Onboarding GHANEPS, a district assembly feed, or a
donor portal is three different ``Source`` rows with three different ``config``
blobs and zero new code.

Two invariants the brief calls out are enforced here:

*   ``robots_allows_automation``.  Where it is False, the source is a discovery
    map only: the harvester records the *sighting* (so first-seen timing is
    still learned) but marks the payload ``discovery_only`` and does not treat
    the source URL as an authoritative fetch.  The authoritative copy is pulled
    from the issuer downstream.
*   **First sighting wins.**  Every channel's observation is recorded, and the
    earliest observation of a tender is flagged ``is_first_sighting`` and bumps
    the source's ``first_sighting_wins_count``.  This is how the system learns
    which channel is actually fastest instead of which one somebody assumed.

The HTTP fetch is isolated in ``_transport`` exactly as the LLM gateway
isolates its call, so tests replace one function instead of monkeypatching
``requests`` globally and accidentally hiding real network use.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from django.utils import timezone

from core.enums import HarvestStrategy, ObjectType
from tenders.ocds import _to_datetime  # reuse the tolerant ISO parser


@dataclass
class HarvestedItem:
    """One notice as read off a source, before it is matched to a Tender."""

    external_id: str = ""
    reference_number: str = ""
    title: str = ""
    description: str = ""
    url: str = ""
    entity_name: str = ""
    published_at: datetime | None = None
    closing_at: datetime | None = None
    object_type: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def is_usable(self) -> bool:
        # A notice needs a title and either a reference or an entity to key on.
        return bool(self.title) and bool(self.reference_number or self.entity_name)


@dataclass
class HarvestStats:
    items_seen: int = 0
    items_skipped: int = 0
    tenders_created: int = 0
    sightings_created: int = 0
    first_sightings: int = 0

    def as_dict(self) -> dict:
        return {
            "items_seen": self.items_seen,
            "items_skipped": self.items_skipped,
            "tenders_created": self.tenders_created,
            "sightings_created": self.sightings_created,
            "first_sightings": self.first_sightings,
        }


# --- transport (the only place a request is made) -----------------------
def _transport(url: str, timeout: int = 30):
    """Fetch a URL and return parsed JSON.  Isolated for the same reason the
    LLM gateway isolates its transport: so tests replace exactly this."""
    import requests

    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


# --- config-path helpers ------------------------------------------------
def _dig(obj, path: str):
    """
    Resolve a dotted path against nested dicts/lists.

    ``"tender.title"`` -> obj["tender"]["title"].  A numeric segment indexes a
    list.  Missing keys yield "" rather than raising, because a source that
    omits a field on some items is normal, not an error.
    """
    if not path:
        return ""
    current = obj
    for segment in path.split("."):
        if isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, list):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return ""
        else:
            return ""
        if current is None:
            return ""
    return current


class FeedStrategy:
    """
    Walk a JSON feed using paths declared in Source.config.

    Expected config shape (all keys optional except items_path):

        {
          "items_path": "releases",           # where the list of items lives
          "fields": {
            "external_id": "ocid",
            "reference_number": "tender.id",
            "title": "tender.title",
            "description": "tender.description",
            "url": "tender.url",
            "entity_name": "buyer.name",
            "published_at": "date",
            "closing_at": "tender.tenderPeriod.endDate",
            "object_type": "tender.mainProcurementCategory"
          }
        }

    That is the entire per-source surface.  A different feed is a different
    ``fields`` map, not a different class.
    """

    def __init__(self, source):
        self.source = source
        self.config = source.config or {}
        self.fields = self.config.get("fields", {})

    def fetch(self):
        payload = _transport(self.source.base_url, timeout=self.config.get("timeout", 30))
        items_path = self.config.get("items_path", "")
        items = _dig(payload, items_path) if items_path else payload
        if isinstance(items, dict):
            items = [items]
        return items or []

    def to_item(self, raw: dict) -> HarvestedItem:
        get = lambda key: _clean(_dig(raw, self.fields.get(key, "")))
        return HarvestedItem(
            external_id=get("external_id"),
            reference_number=get("reference_number"),
            title=get("title"),
            description=get("description"),
            url=get("url"),
            entity_name=get("entity_name"),
            published_at=_to_datetime(get("published_at")),
            closing_at=_to_datetime(get("closing_at")),
            object_type=_map_object_type(get("object_type")),
            raw=raw,
        )

    def items(self):
        for raw in self.fetch():
            if isinstance(raw, dict):
                yield self.to_item(raw)


#: Strategy dispatch.  Only FEED is implemented end-to-end; the others resolve
#: to None so an operator configuring them gets a clear "not yet" rather than a
#: silent no-op.  Adding LISTING later is a new class here, not per-source code.
STRATEGIES = {
    HarvestStrategy.FEED: FeedStrategy,
}


def _clean(value) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _map_object_type(raw: str) -> str:
    mapping = {
        "works": ObjectType.WORKS,
        "goods": ObjectType.GOODS,
        "services": ObjectType.NON_CONSULTANCY,
        "consultingservices": ObjectType.CONSULTANCY,
    }
    return mapping.get((raw or "").strip().lower(), "")


# --- ingestion ----------------------------------------------------------
def _resolve_entity(item: HarvestedItem):
    from catalog.models import ProcuringEntity
    from django.utils.text import slugify

    name = item.entity_name or "Unknown entity"
    entity = ProcuringEntity.objects.filter(name__iexact=name).first()
    if entity is not None:
        return entity
    base = slugify(name)[:200] or "entity"
    slug = base
    n = 2
    while ProcuringEntity.objects.filter(slug=slug).exists():
        slug = f"{base[:250]}-{n}"
        n += 1
    from core.enums import Channel

    return ProcuringEntity.objects.create(
        name=name[:255], slug=slug, channel=Channel.GOV_GHANEPS
    )


def _dedup_hash(item: HarvestedItem, entity) -> str:
    from eligibility.engine import normalise_company_name

    key = "|".join([
        normalise_company_name(entity.name),
        (item.title or "").strip().lower(),
        item.closing_at.isoformat() if item.closing_at else "",
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _match_tender(item: HarvestedItem, entity):
    from tenders.models import Tender

    tender = None
    if item.reference_number:
        tender = Tender.objects.filter(
            entity=entity, reference_number=item.reference_number
        ).first()
    if tender is None:
        tender = Tender.objects.filter(dedup_hash=_dedup_hash(item, entity)).first()
    return tender


def ingest_item(source, item: HarvestedItem, stats: HarvestStats, *, seen_at=None):
    """
    Turn one harvested item into (at most) a Tender and a TenderSighting.

    The sighting is always recorded for a usable item, even when
    ``robots_allows_automation`` is False -- the timing signal is the point of
    recording every channel.  What automation-disallowed sources do *not* get
    is their URL trusted as an authoritative notice fetch; the payload is
    marked so downstream code fetches the issuer's copy instead.
    """
    from tenders.models import Tender, TenderSighting

    stats.items_seen += 1
    if not item.is_usable:
        stats.items_skipped += 1
        return None, None

    seen_at = seen_at or timezone.now()
    entity = _resolve_entity(item)
    tender = _match_tender(item, entity)
    if tender is None:
        tender = Tender.objects.create(
            entity=entity,
            reference_number=item.reference_number[:160],
            ocid=item.external_id[:128] if item.external_id.startswith("ocds") else "",
            title=item.title,
            description=item.description,
            object_type=item.object_type or ObjectType.WORKS,
            published_at=item.published_at,
            closing_at=item.closing_at,
            source_url="" if not source.robots_allows_automation else item.url[:500],
            dedup_hash=_dedup_hash(item, entity),
            raw_text="\n\n".join(t for t in (item.title, item.description) if t),
        )
        stats.tenders_created += 1

    payload = dict(item.raw)
    if not source.robots_allows_automation:
        payload = {"discovery_only": True, "notice_url": item.url, "raw": item.raw}

    sighting, created = TenderSighting.objects.get_or_create(
        tender=tender, source=source,
        defaults={"first_seen_at": seen_at, "url": item.url[:500], "raw_payload": payload},
    )
    if not created:
        return tender, sighting
    stats.sightings_created += 1

    # First sighting wins: is this the earliest observation of this tender?
    earliest = (
        TenderSighting.objects.filter(tender=tender)
        .order_by("first_seen_at")
        .first()
    )
    if earliest is not None and earliest.pk == sighting.pk:
        # Demote any previously-flagged winner (a later-arriving but
        # earlier-timestamped sighting can legitimately take the crown).
        TenderSighting.objects.filter(tender=tender, is_first_sighting=True).exclude(
            pk=sighting.pk
        ).update(is_first_sighting=False)
        if not sighting.is_first_sighting:
            sighting.is_first_sighting = True
            sighting.save(update_fields=["is_first_sighting"])
            _bump_first_sighting_counts(tender)
        stats.first_sightings += 1

    return tender, sighting


def _bump_first_sighting_counts(tender):
    """
    Recompute first_sighting_wins_count deltas for the affected sources.

    Kept simple: when the winning sighting changes, the previous winner's
    source loses a win and the new one gains it.  Because the flag is
    idempotent per tender, recomputing from the flag is safe.
    """
    from django.db.models import Count

    from tenders.models import Source, TenderSighting

    # Recount from the authoritative flag so a demotion is reflected too.
    winners = (
        TenderSighting.objects.filter(is_first_sighting=True)
        .values("source_id")
        .annotate(n=Count("id"))
    )
    counts = {row["source_id"]: row["n"] for row in winners}
    for source in Source.objects.all():
        new_count = counts.get(source.pk, 0)
        if source.first_sighting_wins_count != new_count:
            source.first_sighting_wins_count = new_count
            source.save(update_fields=["first_sighting_wins_count"])


def harvest_source(source, *, stats: HarvestStats | None = None) -> HarvestStats:
    """
    Run one source end to end: fetch, parse per config, ingest sightings.

    Records ``last_harvested_at`` and clears/sets ``last_error`` so an operator
    can see at a glance which sources are healthy.  A fetch failure is captured
    on the row, not raised, so one broken source does not stop a sweep of many.
    """
    from tenders.models import Source

    stats = stats or HarvestStats()
    strategy_cls = STRATEGIES.get(source.strategy)
    if strategy_cls is None:
        source.last_error = f"strategy {source.strategy} not implemented"
        source.save(update_fields=["last_error"])
        return stats

    strategy = strategy_cls(source)
    try:
        for item in strategy.items():
            ingest_item(source, item, stats)
        source.last_error = ""
    except Exception as exc:  # network, malformed feed, bad config path
        source.last_error = f"{type(exc).__name__}: {exc}"[:2000]
    source.last_harvested_at = timezone.now()
    source.save(update_fields=["last_harvested_at", "last_error", "first_sighting_wins_count"])
    return stats
