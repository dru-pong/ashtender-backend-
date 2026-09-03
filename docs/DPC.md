# Data Protection Commission — launch gate

AshKop holds other companies' tax certificates, SSNIT clearances, audited
accounts, bank references, personnel CVs and payment histories. That makes
AshKop a **data controller** under the Data Protection Act 2012 (Act 843), and
registration with the Data Protection Commission is a **launch gate**, not a
compliance chore to be done later.

This file exists because the obligation is easy to discover late, and late is
expensive: operating as an unregistered controller is an offence, and the
remedy is not retroactive.

---

## Why this is not optional

Three separate things trigger it:

1. **Holding third-party compliance documents.** A tenant's audited accounts
   name their auditor; a CV names an individual with their qualifications and
   employment history. That is personal data belonging to people who are not
   AshKop's customers.

2. **Cross-border transfer.** Document images are sent to a hosted inference
   provider outside Ghana. Section 27(1) requires that a data controller
   registering with the Commission declare whether personal data is transferred
   outside the country, and where to. `LLM_PROVIDER_JURISDICTION` in settings
   records the answer, and it must match what was declared.

3. **Processing on behalf of tenants.** Between AshKop and each tenant there is
   a controller/processor relationship that needs to be written down, because
   the tenant remains answerable for their own subjects' data.

---

## Before launch

- [ ] Register as a data controller with the Commission.
      https://www.dataprotection.org.gh/ — +233 256301533
- [ ] Declare the cross-border transfer, naming the provider and jurisdiction.
      Keep it consistent with `LLM_PROVIDER_JURISDICTION`.
- [ ] Write the data processing agreement that ships with the tenant contract.
- [ ] Publish a privacy notice covering: what is held, why, how long, who it is
      shared with, and how a subject exercises their rights.
- [ ] Name a contact point for subject access and erasure requests.
- [ ] Decide and document retention. Section IV of the PPA Standard Tender
      Documents needs a five-year lookback, so five years is the floor for
      financial records — but a superseded tax clearance from 2019 has no such
      justification and should not be kept because deleting it was never
      scheduled.

---

## What the code already does about it

Not a substitute for registration. Listed so that a compliance review can see
the technical measures rather than take them on trust.

- **Row-level isolation that fails closed.** `core/tenancy.py` refuses to
  evaluate a `TenantOwned` queryset that was never scoped to a tenant, at fetch
  time. Tests in `core/tests/test_tenancy.py` and
  `core/tests/test_api_isolation.py`.

- **Exactly two sanctioned cross-tenant readers**, the PayerScore aggregation
  and Django admin, both of which must pass a written reason to
  `across_tenants()`, which logs a warning naming the model and — for admin —
  the staff user. An AST-parsing test enforces the allowlist.

- **Prompts are not retained.** `LLMCall.prompt_preview` is truncated to 500
  characters, because a full prompt can contain another firm's audited
  accounts.

- **Aggregates only, above a floor.** `PayerScore` has no tenant column at all,
  and nothing is published until both the contract and distinct-tenant floors
  are met. Unpublishable rows are filtered out in the queryset rather than the
  serializer, so they are unreachable by pk as well as unlisted.

- **Relationship records never leave their tenant.** `bids.Interaction` is not
  aggregated, shared or surfaced to another tenant in any form. Tested by
  asserting the PayerScore aggregator does not reference it.

- **Notes are built to be read by an auditor.** Offering anything of value to a
  procurement official carries debarment of one to six years, and a donor fraud
  finding means World Bank or AfDB blacklisting. `Interaction` is designed to be
  auditable without embarrassment. That is a design requirement, not a
  disclaimer.

---

## Open

- Whether tenant document storage needs to stay in-country. Not yet
  established; affects hosting choice and should be settled before it is
  expensive to change.
- Retention schedule per document type, once the five-year floor is separated
  from the things that have no reason to persist.
