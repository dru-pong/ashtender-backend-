# AshTender

Per-company legal eligibility for Ghanaian public procurement.

Incumbent aggregators match tenders to companies by category and location.
Nobody computes whether *this specific company* is legally able to bid *this
specific tender* — which is a different question, and the one that decides
whether a bid is wasted effort. AshTender computes it, shows its working, and
routes what it cannot decide to a human.

Design partner (tenant zero) is **Shamma Rohi Limited**, a Sunyani road and
bridge contractor.

---

## The three ideas the code is organised around

**1. A verdict is never a boolean.** `PASS` / `FAIL` / `UNKNOWN` / `WAIVABLE`,
and `UNKNOWN` is the important one. It is how the system says "a person needs
to look at this" instead of quietly binning a tender the firm could have won.
Ghana's published contractor registers lag reality by months, so a firm can
hold a valid certificate that no current list reflects. Reporting that as a
definite bar would be both wrong and expensive.

**2. The model extracts, Python decides.** A vision model may report that a
notice demands a Labour Certificate. Only `eligibility/engine.py` decides
whether that requirement is met — and that module contains zero references to
the LLM app, asserted by test. Verdicts are therefore reproducible,
explainable, and unaffected by a provider outage.

**3. Every finding carries its evidence.** The tender sentence that imposed the
requirement, and the document that answers it. A refusal that cannot quote a
source and name a file does not ship. A system that tells a contractor they are
ineligible without showing its work will not be believed, and should not be.

---

## Isolation

Tenants are direct competitors bidding the same tenders, so isolation is
existential rather than hygienic. `core/tenancy.py` implements it as a queryset
that **refuses to evaluate** until it has been scoped:

```python
Document.objects.all()                    # raises TenantScopeMissing
Document.objects.filter(title__icontains="TCC")   # also raises: not a tenant scope
Document.objects.for_org(org)             # fine
org.vault_document_set.all()              # fine: arrives as filter(organization=org)
document.pages.all()                      # fine: parent row belongs to one tenant
```

`count()`, `exists()`, `aggregate()`, `update()` and `delete()` are all guarded
too, because a leaked count is still a leak.

Crossing tenants is legitimate in exactly two places — the PayerScore
aggregation and Django admin — and both must pass a written reason to
`across_tenants()`, which logs a warning. An AST-parsing test enforces that
allowlist, so a third caller arrives as a failing test rather than a quiet
import.

Organization resolution is **membership-gated**: the URL segment and the
`X-AshTender-Org` header are treated as requests, never as grants. A header
naming a different organization than the path is refused rather than
reconciled. Note that Django middleware runs *before* DRF authenticates, so on
a token-authenticated request the middleware can resolve nothing — the
authoritative resolution lives in `core/viewsets.TenantSafeQuerySetMixin`,
which runs after authentication.

---

## Layout

```
config/         settings split (base/dev/prod/test), celery app, URL surface
core/           tenancy guard, enums, middleware, DRF bases, admin base, health
catalog/        global reference data + the classification notation parser
tenancy/        Organization, Membership, CompanyProfile
vault/          documents, extraction with bounding boxes, the five-year record
tenders/        tenders, sightings, register and debarment oracles, search
eligibility/    the engine: pure rules, orchestration, immutable results
receivables/    contracts, IPCs, arrears claims, PayerScore
llm/            routing table, call ledger, gateway, JSON schemas
bids/           bids, JV teams, interactions, the action-request work queue
docs/           DPC.md (launch gate), OPEN_QUESTIONS.md
```

`eligibility/rules.py` is pure — no database, no clock of its own — so the
arithmetic that decides whether a firm can bid is testable without fixtures.
There is a test asserting it contains no `.objects` reference.

---

## Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill it in

# Postgres in production. pg_trgm is required, not optional:
#   CREATE EXTENSION IF NOT EXISTS pg_trgm;
python manage.py migrate
python manage.py seed_catalog
python manage.py seed_llm_routes
python manage.py seed_tenant_zero
python manage.py createsuperuser
python manage.py runserver
```

All three seeds are idempotent — run them on every deploy.

The test suite runs on sqlite so it needs no Postgres:

```bash
DJANGO_SETTINGS_MODULE=config.settings.test python manage.py test
```

Postgres-only features (full-text search, trigram similarity) are guarded by
`connection.vendor` checks in `tenders/search.py`, in one place, so there is a
single answer to "which database am I on?".

Background workers:

```bash
celery -A config worker -l info
celery -A config beat -l info
```

---

## API surface

Two shapes. Tenant-owned data lives under `/api/v1/orgs/{org}/...`, so a
request that forgets the organization does not resolve at all rather than
falling back to a default tenant. Global data lives at the top level.

```
GET  /api/v1/health/                              public; engine version + db
     /api/v1/orgs/{org}/documents/                + /{id}/confirm/, /expiring/
     /api/v1/orgs/{org}/classifications/
     /api/v1/orgs/{org}/financial-years/
     /api/v1/orgs/{org}/past-projects/
     /api/v1/orgs/{org}/contracts/
     /api/v1/orgs/{org}/ipcs/                     + /aging/
     /api/v1/orgs/{org}/eligibility/              + /run/, /needs_human/
     /api/v1/orgs/{org}/bids/
     /api/v1/orgs/{org}/interactions/
     /api/v1/orgs/{org}/action-requests/
     /api/v1/tenders/?eligible_for_me=true        the one crossing point
     /api/v1/tenders/search/?q=
     /api/v1/entities/{id}/                       dossier: contacts, awards, scores
     /api/v1/payer-scores/                        publishable rows only
     /api/v1/catalog/...                          regions, schemes, caps, specs
```

`?eligible_for_me=true` reads **precomputed** `EligibilityCheck` rows rather
than running the engine inline. Running it inline would be slow and, worse,
would make the same URL return different answers depending on when it was
called — which makes "why did it say that on Tuesday?" unanswerable.

It deliberately excludes `UNKNOWN`: that list answers "what can I bid right
now", and an unresolved question is not a yes. `UNKNOWN` tenders live at
`/api/v1/orgs/{org}/eligibility/needs_human/`, which is a different question.

---

## Ghana-specific things that are easy to get wrong

**Two incompatible classification schemes.** Roads & Highways runs A/B/C/S;
Works & Housing runs D/E/G/K. They are not interchangeable — a firm classified
A1B1 by MRH holds nothing at all in the MWHWR system.

**Class 1 is the *highest* capacity.** Every comparison reads "lower is
better". `class_is_satisfied()` exists as its own function purely so the
inversion is tested in one place; getting it backwards would screen a class-1
firm out of every class-3 tender.

**Class ranges are per letter.** MWHWR E runs to 3 and G runs to 2. Assuming
1–4 everywhere would invent classes nobody can hold and produce confident
FAILs against them, so `WorkCategory.max_financial_class` is required and
`validate_against_catalog()` rejects `E4`.

**Value caps are unconfirmed.** Seeded `is_stale=True`; see
`docs/OPEN_QUESTIONS.md` item 3.

**Funding source is the most predictive field for payment delay**, and no
document states it. A five-minute phone call does, which is why
`Interaction.funding_source_learned` exists and why it is the highest-value
question on a call brief.

**"NGO" is a category of buyer, not a category of work.** `Channel` is client
type; `ProcurementDomain` is what the work is. Aggregators conflate them.

---

## Boundaries

AshTender assembles and stops. A person submits — `Bid.submission_evidence` is
a receipt, not an integration.

It does not price the Bill of Quantities. Pricing the BoQ *is* the bid, and it
is the contractor's judgement. `Bid.priced_by` records who did.

It tracks and documents claims. It does not chase payment. Blur that line and
AshKop gets blamed for Treasury cash flow.

---

## Status

238 tests passing. Built: the isolation layer, the eligibility engine, the LLM
gateway with cache/repair/breaker, receivables with PayerScore, the catalogue
and its seeds, the API surface, the admin, and the nightly tasks.

Not built: the OCDS ingester and the discipline taxonomy mined from real tender
titles, source harvesters beyond the model layer, and the React front end.

Two answers block calibration. Both are in `docs/OPEN_QUESTIONS.md` and both
are seeded as open `ActionRequest` rows: whether tenant zero holds a current
MRH classification, and the real IPC dates behind the Bechem claim.
