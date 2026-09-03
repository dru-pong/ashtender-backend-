# Open questions

Every item here is a thing the code currently guesses, refuses to guess, or
routes to a human. They are listed in the order that unblocks the most work.

The rule this file exists to enforce: an unknown is stored as an unknown. A
blank TIN, a null shareholding percentage and a null IPC date are the true
state of knowledge, and filling them with plausible values would corrupt the
only calibration data the engine has.

---

## 1. Does Shamma Rohi Limited hold a current MRH classification?

**Blocks:** eligibility calibration for tenant zero. Every road tender
currently returns UNKNOWN on classification — correct, but not useful.

**What is established.** A1B1 (category A roads/airports and category B
bridges/culverts, financial class 1 in both), valid 02-Apr-2020 to 02-Apr-2022,
entry 415 on the MRH list of contractors in good standing as at 18 June 2021.
The firm is **not** among the 233 contractors on the reclassification notice
effective 1 January 2023.

**What that absence does not tell us.** Whether the firm failed to reapply,
applied and was omitted, applied late, or holds a certificate issued outside
the published cycle. The published registers lag reality by months, which is
precisely why `_classification_findings` emits UNKNOWN here rather than FAIL.

**Ask:** is there a physical MRH classification certificate dated after
1 January 2023? If so — which letters, which financial classes, and what
valid-from / valid-to dates are printed on it?

Seeded as an open `ActionRequest` of kind `VERIFY_CLASSIFICATION`, priority 100.

---

## 2. The real IPC sequence behind the Bechem claim

**Blocks:** PayerScore. Nothing else in the system can be derived from public
data and this cannot.

The Auditor-General's schedule of outstanding claims as at 31 December 2024
records GH¢12,473,675 against *Upgrading of Selected Roads in Bechem Town (The
Whole, Kwasu, And Mansen) Phase 2*. That figure is a submitted claim or
commitment amount, not a confirmed clean payable: the 2025 validation exercise
rejected roughly GH¢12.1bn of about GH¢90bn in payables, including GH¢125.65m
of duplicated MRH claims. Nothing specific to this firm was found either way.

**Ask:** the individual IPCs behind that figure, and for each one the date
submitted, date certified, date paid, and amount paid. Also the oldest
still-uncertified IPC across all contracts.

Seeded as one placeholder IPC, `AG-2024-CLAIM`, with **null** submitted /
certified / paid dates. Seeded as an open `ActionRequest` of kind
`CALL_ENTITY`, priority 90.

---

## 3. Financial class value caps

**Affects:** every `VALUE_CAP` finding.

The circulating figures — class 4 ≤ USD 75k, class 3 ≤ 200k, class 2 ≤ 500k,
class 1 unlimited — trace to Eyiah (2004) and Dansoh (2005). No current primary
source confirms either the amounts or that the denomination is still USD.

Handled by seeding every `FinancialClassCap` with `is_stale=True`, which the
engine honours two ways: a value within `ELIGIBILITY_CAP_BOUNDARY_FRACTION` of
a cap yields UNKNOWN, and a stale cap can never produce FAIL on its own. There
are tests for both.

**Resolution path:** MWHWR Classification Unit (Room 39, 1st Floor, P.O. Box
M43, Ministries, Accra; +233 577902988 / +233 577902933) and the MRH Road
Classification Committee. Ask for the current schedule of financial limits by
class, and whether the limits are denominated in cedis or dollars.

---

## 4. Company registry identifiers

ORC registration number, TIN, incorporation date and shareholding split are all
absent from the free web. The ORC entity search is paid.

`ghanaian_shareholding_pct` being null is why
`is_domestic_contractor_by_ownership` returns **None** rather than False.
Limbs (a) and (b) of the Act 914 domestic-contractor test cannot be evaluated
without it, and reporting an unrecorded ownership split as a failed test would
be worse than reporting nothing.

---

## 5. Both classification registers are PDF

Confirmed, and it is why `ClassificationScheme.register_ingestible` is False on
every row and asserted False by test. Register verification therefore runs
through VLM extraction plus human confirmation, not an HTTP fetch of a
machine-readable list.

Consequence for matching: register names never match tenant-entered legal names
exactly ("Shamma Rohi Limited" against "SHAMMA ROHI COMPANY LTD"), so matching
is fuzzy — pg_trgm on Postgres, a narrower normalised-exact plus containment
pass on sqlite. A match is a suggestion for a human, never an automatic
identity assertion.

---

## 6. The 251 unloaded MMDAs

10 of 261 districts are seeded, all confirmed against a published source.
`Region.district_seed_complete` is False on 15 of 16 regions and asserted so by
test; Ahafo is the exception because Bechem is the capital of Tano South.

Load the rest with `manage.py load_districts <csv> --mark-complete <REGION>`.
Anything that filters tenders by district must check the flag before presenting
itself as comprehensive.

---

## 7. PPA debarment list

The World Bank and AfDB exclusion lists are public and machine-checkable. A
public PPA debarment list has not been located. `DebarmentEntry` carries a
`PPA` authority value for hand entry when a circular surfaces one.

---

## 8. STD variant for the 5M–15M band

Medium Works covers 200k–15M and Major Works starts at 5M, so 5M–15M is
genuinely ambiguous. `Tender.std_variant` is therefore **stored** as declared or
extracted, never derived from value.
