# Local Travel Policy Rules — v1

**Status: implemented, migration applied and verified against the dev database.** This doc
records the design decided for enforcing a first slice of
`backend/assets/Rules_Consolidated.xlsx` ("Reimbursement Rules", 38 rows) against submitted
claims, and why the scope is narrower than the full sheet.

## hold_reason — a plain-text explanation of why an item/claim is held

Follow-up addition: neither `expense_items` nor `claims` had any single plain-text field
explaining *why* an item landed on `Policy_Hold`/`Fraud_Flag` — only nested JSON
(`policy_validation`/`travel_policy_validation`.reasoningSummary, `fraud_flags`) that a client has
to know to dig into, and the one frontend component that tried to show it
(`ClaimDetailModal.tsx`) read a claim-level `policyValidation` key that hasn't existed since the
multi-item refactor (see below).

Migration `0017_hold_reason_columns` adds `hold_reason` (nullable `Text`) to both tables:

- `expense_items.hold_reason` — set by `ClaimService._build_hold_reason` (new static method,
  `app/services/claim_service.py`) immediately after `_route_item` decides the item's status.
  Mirrors `_route_item`'s own precedence exactly (fraud rationale for `FRAUD_FLAG`; otherwise
  whichever of policy/travel-policy/classification actually failed or asked for review, joined
  into one sentence), so the message never cites a reason that did not actually drive the
  decision. `None` for a clean `AUTO_APPROVED` item.
- `claims.hold_reason` — a roll-up across every held item (`"Item #2 (Meals): <reason> | Item #3
  (...): <reason>"`), built once per claim in `ClaimService._process` right after the per-item
  loop. `None` once nothing on the claim is held.

Exposed on the wire as `holdReason` on both the item and claim shapes
(`app/services/mappers.py::item_to_dict`/`claim_to_dict`).

**Frontend**: `ExpenseClaim.holdReason?: string | null` added to `frontend/src/types.ts`;
`ClaimDetailModal.tsx` renders it as an amber banner directly under the status badge in the modal
header, visible regardless of the deeper legacy-shape mismatch described next. Deliberately scoped
to just this one field — a bigger, separate mismatch was found while investigating (`App.tsx`
feeds `/api/claims`'s real multi-item response straight into the legacy single-item `ExpenseClaim`
type with no adapter, so most other flat claim-level fields in that modal likely read `undefined`
against real data today); fixing that is out of scope here and left for a separate task.

## Verification

- `alembic upgrade head` applied `0016_claim_policy_rules` cleanly against the real (AWS RDS,
  SSH-tunnel) dev database — single head, `alembic current` confirms `0016_claim_policy_rules`.
- Confirmed by direct query against that database: `claim_policy_rules` exists with all 19
  expected columns, and all 9 seeded rows are present with the exact values in the table above
  (4 conveyance-cap bands, 4 meal-allowance-cap bands, 1 hotel prohibition). `expense_items` has
  its 3 new columns (`travel_type`, `duration`, `travel_policy_validation`).
- `python -c "import app.models"` and `from app.main import app` (69 routes) both succeed —
  models, DI wiring (`app/core/deps.py`), and service construction are consistent.
- All touched files pass `python -m py_compile`.
- Full `pytest` suite run against the live dev database: **1057 passed, 723 skipped, 9 failed
  (before fixes), 2 errors.** After investigation:
  - 7 of the 9 failures are pre-existing and unrelated to this change (never touched by this
    diff): `test_ai_architecture.py`, `test_ai_knowledge.py`, 4× `test_auth_login.py` (a real,
    pre-existing bug — `app/api/auth.py:125` reads `.id` off a `SimpleNamespace`), and 2×
    `test_claim_state_machine.py` (the frontend-contract test not yet updated for the pre-existing
    `Withdrawn` status).
  - 1 failure was genuinely caused by this change and has been fixed:
    `test_migrations.py::test_revision_graph_is_complete_and_joined` hard-codes the migration
    graph by design ("adding a revision should require updating this test") — updated to include
    `0016_claim_policy_rules` and its edge from `0015`.
  - The 2 errors (`test_full_upgrade_downgrade_upgrade_round_trip`,
    `test_downgrade_to_0007_restores_the_receipts_tables`) are a pre-existing, structural
    environment limitation, not a bug in this migration: both build their throwaway-database
    engine directly from `settings.database_url` (the raw AWS RDS hostname) instead of through
    `app.core.database.get_engine()`'s SSH tunnel, so they cannot reach the database in this
    sandbox at all, regardless of what migration is at head. Every `db_engine`-fixture test is
    structurally in the same position, which is why the skip count (723) matches almost exactly
    what an entirely unrelated earlier session reported before any of this work started.
  - This migration's own `upgrade()`/`downgrade()` were instead verified directly: `alembic
    upgrade head` (tunnel-aware) applied cleanly, and the table/columns/seed rows were confirmed
    by direct query as above. The downgrade path (`op.drop_column`/`op.drop_table`/enum drops) was
    reviewed by inspection but not executed end-to-end, for the same environment reason.
- `0017_hold_reason_columns`: `alembic upgrade head` applied cleanly on top of `0016` against the
  same live dev database; `hold_reason` confirmed present on both `expense_items` and `claims` by
  direct query. `test_revision_graph_is_complete_and_joined` updated again and passes. All touched
  files (`claim.py`, `expense_item.py`, `claim_service.py`, `mappers.py`, the migration) pass
  `python -m py_compile`; the app still imports and boots. Isolated `pytest` runs of
  `test_claim_service_lifecycle.py` consistently skipped the DB-fixture tests in this session (the
  same connection-timeout pattern as above, worse when a file is run in isolation rather than
  inside the full suite where the tunnel is already warm) — a full-suite re-run to get a live
  regression signal on `_build_hold_reason`/`_route_item` specifically was not completed in this
  session; do that before calling this addition fully regression-tested.

## Source data and why the scope is narrow

The sheet has columns `Rule Name | Rule Description | Amount | Employee Grade | Travel Type |
Duration | Employee (Team/Individual)`, 38 rows split across Local / Domestic / International
travel. Three properties of the data make a naive "one row = one table row with an amount"
design break:

- **21 of 38 rows have `Amount = 0`**, which almost always means "not an amount-capped rule" (a
  prohibition, a sequencing rule, a document requirement, an approval-routing threshold) — not
  "the cap is literally ₹0".
- **~11 rows encode a range by grade band or city/country tier in prose** (e.g. "Rs 300-1,200/day
  by grade"), not a single number.
- **The sheet's "Employee Grade" is "Band 1-4" / "All Grades"**, a different vocabulary from this
  codebase's `EmployeeGrade` enum (`L1-L5, Director, VP`).

Filtering the 38 rows down to ones that are (a) a plain amount-cap or flat prohibition, (b) keyed
only on category + travel type + grade + duration (no city tier, country group, per-diem,
advances, or document checks — none of which exist as domain concepts in this codebase yet), and
(c) map onto an existing `expense_categories` row, leaves **3 rules, all "Local" travel**:

| Rule (sheet) | Category | Type | Cap (band range, interpolated) |
|---|---|---|---|
| Local Conveyance Entitlement / Local Cab-Ride-Hailing Booking (merged — same cap) | Taxi / Cab | `AMOUNT_CAP` | ₹1200 / 900 / 600 / 300 per day (Band 1→4) |
| Local Meal Allowance | Meals | `AMOUNT_CAP` | ₹300 / 250 / 200 / 150 per day (Band 1→4) |
| Local Hotel Booking Restriction / Local Per-Diem-Lodging Exclusion (merged — same restriction) | Hotel / Lodging | `PROHIBITED` | — |

Everything else in the sheet (per-diem calculations, travel advances, document/passport checks,
city- and country-tier hotel caps, booking/itinerary linkage, approval-routing thresholds) needs
domain concepts this codebase doesn't have yet (trips, per-diem, advances, city/country masters)
and is deliberately out of scope for this phase — nothing from the sheet is deleted or hidden, it
simply isn't enforced yet.

## Schema

**New enums** (`app/models/enums.py`): `TravelType` (`Local`/`Domestic`/`International`),
`ExpenseDuration` (`Day`/`Month` — mirrors the sheet's Duration column verbatim), `GradeBand`
(`Band 1`..`Band 4`).

**`expense_items` gains** (all nullable — no effect unless a rule scopes on them):
`travel_type`, `duration`, `travel_policy_validation` (JSONB, kept separate from the existing
`policy_validation` column to avoid mixing two different report shapes).

**New table `claim_policy_rules`** — same versioned/effective-dated lifecycle idiom as the
existing `policy_rules` table (`code`+`version` unique, never edited in place):

```
id, code, version, name, description,
category        nullable = wildcard, matches expense_items.category
travel_type     nullable = wildcard
grade_band      nullable = wildcard
duration        nullable = wildcard
rule_type       AMOUNT_CAP | PROHIBITED
amount, currency        the cap (AMOUNT_CAP only; NULL for PROHIBITED)
effective_date, expiration_date, priority, is_active
created_by_sub, created_at, updated_at
```

**Grade-band mapping** is a static Python constant
(`app/services/policy_engine.py:GRADE_TO_BAND`), not a DB table — a fixed mapping, not something
edited at runtime in v1: `VP`/`Director` → `BAND_1`, `L5` → `BAND_2`, `L3`/`L4` → `BAND_3`,
`L1`/`L2` → `BAND_4`.

## Engine

`evaluate_travel_policy(items: list[dict], rules: list[dict]) -> list[dict]`
(`app/services/policy_engine.py`) — whole-claim scope, one call per claim, parallel to
`evaluate_expense_policy` (unchanged, still runs per item as before; this is an *additional*
layer, not a replacement):

1. For each item, resolve the claim's `employeeGrade` to a `gradeBand` via `GRADE_TO_BAND`.
2. Match rules where `category`/`travelType`/`gradeBand`/`duration` each satisfy
   `rule value IS NULL OR rule value == item value`.
3. `PROHIBITED` match → item fails, `eligibleAmount = 0`.
4. `AMOUNT_CAP` match → `eligibleAmount = min(item.amount, rule.amount)`, compared **in native
   currency only** (rule currency vs. item currency) — never FX-converted. A currency mismatch is
   flagged for manual review, never silently compared.
5. No match → no travel-policy opinion; the item's existing category-based `policy_validation`
   still applies unchanged.

Verification is deterministic Python (arithmetic + lookups) — no LLM is involved in judging
pass/fail against a rule. An LLM is only ever upstream, extracting the raw amount/vendor/category
off a receipt image; by the time this engine runs, the values are already structured data.

## Integration

Wired automatically into the existing claim submission pipeline
(`ClaimService._process`), alongside the existing per-item policy evaluation — not a separate
opt-in endpoint. A `ClaimPolicyRuleRepository` dependency (optional constructor arg on
`ClaimService`, same pattern as `decision_memory`/`duplicate_detection` — `None` means "byte
identical to before this existed") fetches the effective ruleset once per claim; the report is
stored per item on `expense_items.travel_policy_validation`.

`ClaimService._route_item` gains one more condition: a travel-policy report that failed or asked
for manual review routes that item to `POLICY_HOLD`, the same precedence a category-policy
violation already uses. `_roll_up_status` is unchanged — a claim with any `POLICY_HOLD` item
still rolls up to `Manager_Review`.

## Explicitly deferred

Per-diem, travel advances, passport/visa/document checks, city-tier and country-group hotel caps,
booking/itinerary linkage, approval-routing thresholds, and Domestic/International travel-type
rules generally — all of these need domain concepts (trips, per-diem calculation, advances, city
and country reference masters) that don't exist in this codebase yet. Extending this table's
scope columns (already generic: category/travel_type/grade_band/duration) to more rules is
possible once those domains exist; nothing about this phase's schema needs to change to add rows
later, only new `rule_type` values and matching domain data.
