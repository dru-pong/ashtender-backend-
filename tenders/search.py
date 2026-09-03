"""
Tender search.

Postgres gets full-text search plus trigram similarity; sqlite gets icontains.
The vendor check lives here rather than in a viewset so there is exactly one
place where "which database am I on?" is asked, and so the test suite can run
without Postgres while production still gets the real index.
"""
from django.db import connection
from django.db.models import Q


def search_tenders(queryset, query: str):
    """
    Filter tenders by free text.

    Ordering is by published date, not relevance rank, on both backends.  A
    contractor with nine days to closing wants the newest matching notice, not
    the one where the phrase happened to appear three times.
    """
    query = (query or "").strip()
    if not query:
        return queryset

    if connection.vendor == "postgresql":
        from django.contrib.postgres.search import SearchQuery, SearchVector

        vector = (
            SearchVector("title", weight="A")
            + SearchVector("description", weight="B")
            + SearchVector("reference_number", weight="A")
        )
        return (
            queryset.annotate(search=vector)
            .filter(Q(search=SearchQuery(query, search_type="websearch")) | _fallback(query))
            .distinct()
        )

    return queryset.filter(_fallback(query)).distinct()


def _fallback(query: str) -> Q:
    """
    Substring match, always applied.

    Kept alongside FTS on Postgres because tender references like
    "MRH/RF/2026/014" are punctuation-heavy and get mangled by tokenisation,
    while a contractor searching for one expects an exact hit.
    """
    return (
        Q(title__icontains=query)
        | Q(description__icontains=query)
        | Q(reference_number__icontains=query)
        | Q(entity__name__icontains=query)
    )


def similar_company_names(model, field: str, name: str, threshold: float = 0.35):
    """
    Fuzzy company-name matching for register and debarment lookups.

    Needed because register names never match tenant-entered legal names
    exactly -- "Shamma Rohi Limited" against "SHAMMA ROHI COMPANY LTD" -- and an
    exact-match lookup would report a valid classification as absent, which is
    worse than reporting nothing.

    A match is a suggestion for a human. It is never an automatic identity
    assertion, on either backend.
    """
    from eligibility.engine import normalise_company_name

    normalised = normalise_company_name(name)
    if not normalised:
        return model.objects.none()

    if connection.vendor == "postgresql":
        from django.contrib.postgres.search import TrigramSimilarity

        return (
            model.objects.annotate(
                similarity=TrigramSimilarity(field, normalised)
            )
            .filter(similarity__gte=threshold)
            .order_by("-similarity")
        )

    # sqlite: exact on the normalised form, plus a containment pass. Narrower
    # than trigram, which is why pg_trgm is a production requirement and not a
    # nice-to-have.
    return model.objects.filter(
        Q(**{f"{field}": normalised}) | Q(**{f"{field}__icontains": normalised})
    )
