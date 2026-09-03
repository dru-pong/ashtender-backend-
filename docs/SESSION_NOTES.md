# Session notes — data-acquisition build (spec section 4)

This session built the acquisition layer the previous handoff called the single
biggest gap: nothing could get tenders into the system. It is now built, tested,
and green at **291 tests** (was 238).

Read `ASHTENDER_PROJECT_BRIEF.md` and the earlier handoff for design invariants.
This file only records what changed and what you still have to run on real
Postgres, which the build sandbox did not have.

---

## What was added

All new code matches the conventions already in the tree: pure parse separated
from DB writes, transports isolated so tests replace one function, `JSONField`
not `ArrayField`, no new dependencies, tri-state where a fact may be unknown.

**4.2 OCDS ingester** — `tenders/ocds.py`, command `ingest_ocds`.
`parse_release` is pure; `ingest_releases` writes. Populates `Tender`,
`AwardHistory`, `ProcuringEntity.ocds_party_id`. Winner names normalise through
`eligibility.engine.normalise_company_name`, so the competitor graph collapses
suffix variants the way register matching does. Idempotent; entities resolved by
durable party id so a renamed buyer does not split into two nodes.

    python manage.py ingest_ocds --path corpus.json
    cat corpus.ndjson | python manage.py ingest_ocds

**4.5 Discipline mining** — `tenders/disciplines.py`, command `mine_disciplines`.
Mined from real titles only: a `Discipline` node exists solely because a title
created it, `ocds_title_count` is evidence, unmatched titles stay untagged. Once
a tender is tagged, the engine's `SIMILAR_WORKS` finding stops being UNKNOWN
(verified end to end in `tenders/tests/test_disciplines.py`).

    python manage.py ingest_ocds --path corpus.json
    python manage.py mine_disciplines

**4.3 Source harvester** — `tenders/harvest.py`, command `harvest_sources`.
Config-driven FEED strategy: a new source is a `Source` row plus a `fields` map
in `config`, never new code (there is a test asserting exactly that). Honours
`robots_allows_automation` (records the sighting for timing but does not trust
the URL as an authoritative fetch). First-sighting-wins tracking is correct even
when the faster channel is harvested later. Transport isolated in
`harvest._transport`.

    python manage.py harvest_sources            # all active sources
    python manage.py harvest_sources --slug ghaneps-feed

To onboard a source, create a `Source` row (admin or shell) with
`strategy=FEED`, a `base_url`, and a `config` like:

    {"items_path": "releases",
     "fields": {"reference_number": "tender.id", "title": "tender.title",
                "entity_name": "buyer.name", "url": "tender.url",
                "closing_at": "tender.tenderPeriod.endDate"}}

**4.4 Document pipeline** — `vault/rendering.py` (+ `vault/tasks.py`), wired into
`DocumentViewSet.perform_create`. On upload: hash via `compute_hash()` → render
pages with poppler `pdftoppm` (local/free, DPI and longest-edge capped for the
low-RAM VPS) → queue `llm.tasks.extract_document_fields`. Never touches
`verified_by_human` — only the confirm action does (test asserts this).

**System dependency:** poppler must be installed for PDF rendering.
macOS: `brew install poppler`. Debian/Ubuntu VPS: `apt-get install poppler-utils`.
Without it, rendering degrades to zero pages and logs a warning rather than
crashing; extraction then reports `no_pages_rendered`.

**4.6 LLM smoke test** — command `smoke_llm`.
`--probe` makes one real call per active route (operator-run, hits the provider);
`--set-costs` writes real per-1k USD figures onto routes (unit-tested).

    python manage.py smoke_llm --probe
    python manage.py smoke_llm --set-costs '{"DOC_EXTRACT": [0.00018, 0.00054]}'

**Celery beat** — added `harvest-all-sources` (every 4h) and `mine-disciplines`
(nightly 01:30, before the 02:00 eligibility sweep). OCDS ingest is deliberately
NOT on the beat: it needs an operator-downloaded corpus file, so it stays
on-demand.

**New Celery tasks** (worker and beat do not autoreload — restart them):
`tenders.tasks.{ingest_ocds_file, mine_disciplines_task, harvest_all_sources,
harvest_one_source}`, `vault.tasks.process_uploaded_document`.

No model fields changed, so no new migrations (`makemigrations --check` is
clean).

---

## 4.1 Validate against real Postgres — RUN THIS FIRST on your Mac

The sandbox had no Postgres, so every Postgres-only path (pg_trgm similarity,
SearchVector FTS in `tenders/search.py`, the partial unique constraint on
`Tender`, `DecimalField` rounding in `PayerScore`) is still code-reviewed only.
Validate before trusting any of it:

    createdb ashtender
    psql -d ashtender -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"

    # point settings at Postgres via .env (see .env.example: DATABASE_URL / the
    # DB_* vars the prod settings read), then:
    DJANGO_SETTINGS_MODULE=config.settings.prod python manage.py migrate
    DJANGO_SETTINGS_MODULE=config.settings.prod python manage.py seed_catalog
    DJANGO_SETTINGS_MODULE=config.settings.prod python manage.py seed_llm_routes
    DJANGO_SETTINGS_MODULE=config.settings.prod python manage.py seed_tenant_zero

    # then run the full suite against Postgres, not sqlite:
    #   set the test DB to Postgres and run manage.py test
    # watch for: JSONField query differences, the Tender partial unique
    # constraint (condition=Q(reference_number__gt="")), PayerScore Decimal
    # rounding, and the pg_trgm / SearchVector branches in tenders/search.py.

Once green on Postgres, harvest a real feed and mine disciplines against the
`pg_trgm` similarity path with real winner-name variants — that exercises
`tenders.search.similar_company_names` on the production backend for the first
time.

---

## Still deliberately unbuilt (not oversights)

- JV combination logic stays human-reviewed.
- UNKNOWN stays a first-class verdict.
- React front end (Phase 13).
- OCDS ingest is manual-corpus, not a scheduled fetcher (no source URL to trust
  yet; a fetcher would be a source harvester, which the abstraction already
  covers as a DB row when you have one).
