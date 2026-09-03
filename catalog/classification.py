"""
Parsing contractor-classification notation.

Ghanaian notices state classification requirements in a compressed notation:
``D3K3``, ``A1B1``, ``D2/K2 and above``, ``Category K3 or higher``.  Spec
section 3.7 fixes the grammar as ``([A-KS])([1-4])`` repeated, with a trailing
"and above" setting or_better.

Two things this module deliberately does not do:

*   It does not decide eligibility.  It returns parsed requirements; the
    engine in ``eligibility.engine`` compares them against what a company
    holds.
*   It does not validate that a class number exists for a letter.  E under
    MWHWR only runs to 3 and G only to 2, so ``E4`` is unparseable in the real
    world -- but that check needs the database, and is done by
    ``validate_against_catalog``.  Parsing stays pure and testable.

Class numbering is inverted from intuition: 1 is the *highest* capacity.  Every
comparison in this file and in the engine therefore reads "lower is better",
which is worth saying out loud because getting it backwards would silently
screen a tenant out of everything they can actually do.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Letters in use: A-K across both ministries, plus S for steel bridges (MRH).
TOKEN_RE = re.compile(r"(?P<letter>[A-KS])\s*[-/ ]?\s*(?P<klass>[1-9])", re.IGNORECASE)

#: Phrases that widen a stated class to "this class or any better one".
OR_BETTER_PATTERNS = (
    "and above",
    "or above",
    "and higher",
    "or higher",
    "or better",
    "and better",
    "upwards",
    "or above class",
)

#: Phrases that pin a requirement to exactly the stated class.
EXACT_PATTERNS = ("only", "exactly", "strictly")

MRH_LETTERS = frozenset("ABCS")
MWHWR_LETTERS = frozenset("DEGK")


@dataclass(frozen=True)
class ClassRequirement:
    """One (category, minimum class) pair extracted from tender text."""

    letter: str
    min_financial_class: int
    or_better: bool = True
    scheme_code: str | None = None
    source_text: str = ""

    def __str__(self):
        suffix = " or better" if self.or_better else " only"
        return f"{self.letter}{self.min_financial_class}{suffix}"

    def satisfied_by(self, held_class: int) -> bool:
        """Lower class number means higher capacity."""
        if self.or_better:
            return int(held_class) <= self.min_financial_class
        return int(held_class) == self.min_financial_class


@dataclass
class ParseResult:
    requirements: list[ClassRequirement] = field(default_factory=list)
    or_better: bool = True
    unparsed_remainder: str = ""
    raw: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.requirements


def infer_scheme(letter: str) -> str | None:
    """
    Which ministry owns a letter.

    Unambiguous today because the two letter sets are disjoint, but returned as
    Optional so a future overlap surfaces as None rather than a wrong guess.
    """
    upper = letter.upper()
    if upper in MRH_LETTERS:
        return "MRH"
    if upper in MWHWR_LETTERS:
        return "MWHWR"
    return None


def parse_classification_notation(text: str) -> ParseResult:
    """
    Extract class requirements from a fragment of notice text.

    >>> [str(r) for r in parse_classification_notation("D3K3").requirements]
    ['D3 or better', 'K3 or better']
    >>> [str(r) for r in parse_classification_notation("A1B1").requirements]
    ['A1 or better', 'B1 or better']
    >>> parse_classification_notation("Category D2 only").requirements[0].or_better
    False
    """
    result = ParseResult(raw=text or "")
    if not text:
        return result

    lowered = text.lower()
    or_better = True
    if any(pattern in lowered for pattern in EXACT_PATTERNS) and not any(
        pattern in lowered for pattern in OR_BETTER_PATTERNS
    ):
        or_better = False
    result.or_better = or_better

    seen: dict[tuple[str, int], ClassRequirement] = {}
    consumed_spans = []
    for match in TOKEN_RE.finditer(text):
        letter = match.group("letter").upper()
        klass = int(match.group("klass"))
        key = (letter, klass)
        consumed_spans.append(match.span())
        if key in seen:
            continue
        seen[key] = ClassRequirement(
            letter=letter,
            min_financial_class=klass,
            or_better=or_better,
            scheme_code=infer_scheme(letter),
            source_text=text.strip(),
        )

    result.requirements = list(seen.values())
    result.unparsed_remainder = _remainder(text, consumed_spans)
    return result


def _remainder(text: str, spans) -> str:
    """What the regex did not consume, so a human can see what was ignored."""
    if not spans:
        return text.strip()
    kept, cursor = [], 0
    for start, end in sorted(spans):
        kept.append(text[cursor:start])
        cursor = end
    kept.append(text[cursor:])
    return re.sub(r"\s+", " ", "".join(kept)).strip(" ,;/-")


def format_notation(requirements) -> str:
    """Round-trip back to compact notation: [(D,3),(K,3)] -> 'D3K3'."""
    return "".join(f"{r.letter}{r.min_financial_class}" for r in requirements)


def validate_against_catalog(requirements) -> tuple[list, list]:
    """
    Split parsed requirements into (recognised, rejected).

    Hits the database, so it lives apart from the pure parser.  A letter/class
    pair that does not exist in ``WorkCategory`` is rejected rather than
    quietly accepted -- ``E4`` under MWHWR is a typo or an OCR error, and
    treating it as real would produce a confident FAIL against a class nobody
    can hold.
    """
    from catalog.models import WorkCategory

    recognised, rejected = [], []
    catalogue = {
        (wc.scheme.code, wc.letter): wc
        for wc in WorkCategory.objects.select_related("scheme")
    }
    by_letter: dict[str, list] = {}
    for (scheme_code, letter), wc in catalogue.items():
        by_letter.setdefault(letter, []).append(wc)

    for req in requirements:
        candidates = by_letter.get(req.letter, [])
        if req.scheme_code:
            candidates = [c for c in candidates if c.scheme.code == req.scheme_code] or candidates
        match = next(
            (c for c in candidates if c.class_exists(req.min_financial_class)), None
        )
        if match is None:
            rejected.append(
                (
                    req,
                    f"no {req.letter}{req.min_financial_class} exists in the catalogue "
                    f"(letters known: {sorted(by_letter)})",
                )
            )
        else:
            recognised.append((req, match))
    return recognised, rejected
