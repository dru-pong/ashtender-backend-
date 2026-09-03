"""
Mine the discipline taxonomy from real tender titles.

Spec sections 4.1 and 11 phase 4: the taxonomy must be *mined from real
GHANEPS/OCDS titles*, not invented, because an invented tree encodes what a
developer imagined Ghana procures instead of what it procures.

This module honours that in two ways:

*   A ``Discipline`` node is created **only when a real title lands on it**.
    Nothing pre-populates the tree.  ``ocds_title_count`` records how many
    titles matched, so coverage is evidenced rather than asserted -- a node
    with a count of zero cannot exist, because it was never created.
*   The lexicon below is not a taxonomy of imagined work.  It is a set of
    surface patterns describing how Ghanaian works titles actually read -- the
    same templated forms the TITLE_PARSE route documents ("Construction of
    1No. 3-Unit Classroom Block", "Drilling of ... Boreholes", "Reshaping ...
    Feeder Road").  A phrase only becomes a discipline if it is seen in the
    corpus.

The matcher is intentionally conservative: a title that matches nothing is
left untagged rather than forced onto a nearest node, because a wrong
discipline tag produces a confident-but-wrong SIMILAR_WORKS verdict, which is
worse than the honest UNKNOWN the engine already returns for an untagged
tender.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Surface patterns -> (slug, human name, domain code).
#:
#: Each entry is a list of lowercase substrings; a title matches the discipline
#: if ANY substring is present.  These are descriptions of observed title
#: language, ordered so the more specific pattern is tried first.  ``domain`` is
#: the ProcurementDomain code the discipline rolls up to when one exists; None
#: leaves the node's domain null rather than guessing.
LEXICON: list[tuple[str, str, str | None, list[str]]] = [
    # --- roads & drainage ---
    ("feeder-road", "Feeder road works", "road-maintenance",
     ["feeder road", "reshaping", "spot improvement", "regravelling", "re-gravelling"]),
    ("bitumen-surfacing", "Bituminous surfacing", "road-construction",
     ["asphalt", "bitumen", "surface dressing", "surfacing"]),
    ("road-construction", "Road construction & upgrading", "road-construction",
     ["construction of road", "upgrading of road", "rehabilitation of road",
      "upgrading of selected roads", "town roads", "urban roads"]),
    ("culvert-drainage", "Culverts & drainage", "drainage-flood",
     ["culvert", "drainage", "u-drain", "drain construction", "lined drain"]),
    ("bridge", "Bridge works", "bridges-culverts",
     ["bridge", "box culvert", "steel bridge"]),
    # --- buildings ---
    ("classroom-block", "Classroom blocks", "school-infrastructure",
     ["classroom block", "unit classroom", "school block", "kg block"]),
    ("office-building", "Office & institutional buildings", "building-construction",
     ["office block", "office building", "administration block",
      "assembly hall", "court complex"]),
    ("health-facility", "Health facilities", "health-infrastructure",
     ["chps compound", "health centre", "health center", "clinic", "hospital",
      "ward block", "maternity"]),
    ("housing", "Housing & accommodation", "building-construction",
     ["bungalow", "residential", "staff accommodation", "housing units", "quarters"]),
    ("market-structure", "Market & lorry-park structures", "market-infrastructure",
     ["market", "lorry park", "market shed", "market complex"]),
    ("sanitation-facility", "Sanitation facilities", "water-sanitation",
     ["toilet", "latrine", "kvip", "water closet", "wc block"]),
    # --- water ---
    ("borehole", "Boreholes & handpumps", "boreholes",
     ["borehole", "hand pump", "handpump", "drilling of"]),
    ("water-system", "Water supply systems", "water-sanitation",
     ["water supply", "small town water", "pipe extension", "reticulation",
      "overhead tank", "mechanised borehole", "limited mechanis"]),
    # --- electrical ---
    ("electrification", "Rural electrification", "electrical-works",
     ["electrification", "extension of electricity", "lv network", "hv network",
      "street light", "solar street"]),
    # --- goods & supply (works corpus still carries some) ---
    ("furniture-supply", "Furniture supply", "supply-goods-general",
     ["supply of furniture", "dual desk", "mono desk", "school furniture"]),
]

#: Templated quantity/unit tokens, e.g. "1No.", "10 No", "3.2km", "250 m3".
_QTY_RE = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>no\.?|units?|km|m2|m3|m²|m³|nr)\b",
    re.IGNORECASE,
)
_UNIT_CANON = {
    "no": "No.", "no.": "No.", "nr": "No.",
    "unit": "Unit", "units": "Unit",
    "km": "km", "m2": "m2", "m²": "m2", "m3": "m3", "m³": "m3",
}


@dataclass
class TitleMatch:
    slug: str
    name: str
    domain_code: str | None
    quantity: float | None = None
    unit: str = ""


@dataclass
class MineStats:
    titles_seen: int = 0
    titles_matched: int = 0
    titles_unmatched: int = 0
    disciplines_created: int = 0
    tender_tags_written: int = 0
    nodes_touched: set = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "titles_seen": self.titles_seen,
            "titles_matched": self.titles_matched,
            "titles_unmatched": self.titles_unmatched,
            "disciplines_created": self.disciplines_created,
            "tender_tags_written": self.tender_tags_written,
            "distinct_nodes": len(self.nodes_touched),
        }


def parse_quantity(title: str) -> tuple[float | None, str]:
    """Pull the leading templated quantity+unit out of a title, if present."""
    match = _QTY_RE.search(title or "")
    if not match:
        return None, ""
    try:
        quantity = float(match.group("qty"))
    except ValueError:
        quantity = None
    unit = _UNIT_CANON.get(match.group("unit").lower().rstrip("."), match.group("unit"))
    return quantity, unit


def match_title(title: str) -> list[TitleMatch]:
    """
    Which disciplines a single title lands on.  Pure: no database.

    A title can match more than one discipline ("Construction of Classroom
    Block and 6-Seater KVIP" is both classroom-block and sanitation-facility),
    and all matches are returned so the tender is tagged with each.
    """
    lowered = (title or "").lower()
    if not lowered.strip():
        return []
    quantity, unit = parse_quantity(title)
    matches: list[TitleMatch] = []
    seen: set[str] = set()
    for slug, name, domain_code, needles in LEXICON:
        if slug in seen:
            continue
        if any(needle in lowered for needle in needles):
            matches.append(TitleMatch(slug, name, domain_code, quantity, unit))
            seen.add(slug)
    return matches


# --- ingestion (writes) -------------------------------------------------
def _get_or_create_discipline(match: TitleMatch, cache: dict, stats: MineStats):
    from catalog.models import Discipline, ProcurementDomain

    if match.slug in cache:
        return cache[match.slug]

    node = Discipline.objects.filter(slug=match.slug).first()
    if node is None:
        domain = None
        if match.domain_code:
            domain = ProcurementDomain.objects.filter(code=match.domain_code).first()
        # MP_Node roots must be created with add_root, not create().
        node = Discipline.add_root(slug=match.slug, name=match.name, domain=domain)
        stats.disciplines_created += 1
    cache[match.slug] = node
    return node


def mine_titles(tenders, *, tag_tenders: bool = True, stats: MineStats | None = None):
    """
    Mine disciplines from an iterable of Tender rows.

    For each title: find its disciplines, create any node a real title lands on
    for the first time, bump ``ocds_title_count`` on every node it lands on,
    and (optionally) write ``TenderDiscipline`` tags so the eligibility engine's
    SIMILAR_WORKS check can match past projects against the tender.
    """
    from catalog.models import Discipline
    from tenders.models import TenderDiscipline

    stats = stats or MineStats()
    cache: dict = {}
    increments: dict[str, int] = {}

    for tender in tenders:
        stats.titles_seen += 1
        matches = match_title(tender.title)
        if not matches:
            stats.titles_unmatched += 1
            continue
        stats.titles_matched += 1
        for match in matches:
            node = _get_or_create_discipline(match, cache, stats)
            increments[node.slug] = increments.get(node.slug, 0) + 1
            stats.nodes_touched.add(node.slug)
            if tag_tenders:
                _, created = TenderDiscipline.objects.update_or_create(
                    tender=tender, discipline=node,
                    defaults={"quantity": match.quantity, "unit": match.unit[:16]},
                )
                if created:
                    stats.tender_tags_written += 1

    # Apply counts in one pass.  Add rather than overwrite: a later corpus is
    # more evidence of coverage, not a replacement for the earlier count.
    from django.db.models import F

    for slug, delta in increments.items():
        Discipline.objects.filter(slug=slug).update(
            ocds_title_count=F("ocds_title_count") + delta
        )
    return stats
