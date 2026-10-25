# Actual model token usage

Status: implemented source contract; real PostgreSQL and deployment acceptance remain separate.
Owner: Runs. Claude SDK parsing stays in the Execution adapter.

## Facts and receipt transaction

The administrator usage page reads only `model_token_usage_deltas`, never the
Run-bound model, legacy Run token counters, or cost coverage. Four bigint counters
are retained: `input_tokens`, `cache_read_input_tokens`,
`cache_creation_input_tokens`, and `output_tokens`. Input total is the first
three; total tokens includes all four. No prices or monetary estimates are used.

One runner invocation creates a random scope ID and fixes its restore baseline.
Every SDK Result, including intermediate background/continuation Results, emits
a cumulative private `model_usage_snapshot` through the existing callback buffer.
The final callback carries another observation through the same path. Explicit
zero counts are known observations; missing reports are not zeros. Failed and
cancelled executions retain all previously accepted observations. Missing terminal
callbacks leave partial coverage. A terminal model report means reported-final
for that scope, not proof of all provider or subagent consumption.

A fresh invocation has a zero baseline. Restored transcript entries require a
valid matching cost-state **model counter** baseline; cost values do not establish
token validity. Missing baseline, explicit reset, session identity change, missing
models and regressing counters do not create estimates. SDK-only model identities
are preserved; they are not mapped to configured platform models.

`ModelUsageSnapshot` is a standard-library domain dataclass. Its public
`normalize_model_usage_snapshot` validates strict counters, bounded model keys,
baselines and coverage independently of transport. The private Pydantic
`ModelUsageSnapshotPayload` belongs to Sandbox callback contracts; the HTTP
boundary converts it to the domain value. Application/persistence use no
Pydantic model methods. Persisted payloads retain the same JSON representation.

`ModelTokenUsageService` is exported by `runs.api`, composed in bootstrap, and
injected into the callback route. The existing authority locks Run then its exact
Attempt/lease. Receipt creation, observation/delta inserts, scope high-watermark
and final authority recheck share that transaction. The first receipt's platform
`callback_received_at` is the recording time. No new transport, public event,
Worker reconciliation writer or lifecycle state is introduced.

Scope keys are `(tenant_id,run_id,attempt_id,scope_id)`. Private snapshots carry
provider session ID and the immutable model-counter baseline. The Context adapter
resolves the authoritative provider ID from the exact platform provider turn and
its Run tenant/workspace/user/session/agent binding; executor IDs cannot select
another identity. Before the SDK supplies an ID on a fresh invocation, the
platform turn already binds it; an empty fresh baseline is valid. No platform
binding means no counted usage. Runs takes a transaction advisory lock keyed by
tenant/provider session and compares the baseline against every other accepted
scope's absolute cumulative counters. Fresh overlapping re-invocations, missing
baseline, provider mismatch and changed baseline are partial and produce no
estimated delta. A non-overlapping restored invocation is allowed in the same
Attempt or another Run/Attempt; no new execution lifecycle is imposed. Observations additionally
key on `observation_seq`; deltas additionally key on actual model. Exact observation
replays are no-ops across batches. Conflicting same-sequence content marks partial;
old sequences cannot advance cumulative state. Missing models or any regression
reject the entire new cumulative observation, preserve prior trusted counters and
mark partial; there is no per-column MAX or negative delta. The observation table
retains bounded normalized payloads to compare earlier sequence replays without
inventing another digest protocol. Public callback receipt digests retain their
existing authority. Malformed DTOs return validation errors; missing batch identity
returns 409. Lease/Attempt rejection remains the existing callback rejection.

## Administrator API

- `GET /api/ai/admin/usage/users`: `start_date`, `end_date`, `q`, `limit` (default
  50, 1–100), `offset` (default 0).
- `GET /api/ai/admin/usage/users/{user_id}/models`: same dates.

Dates are Beijing inclusive calendar dates converted to a UTC half-open interval.
Default is the last 30 Beijing calendar days including today. Invalid/reversed
ranges return 422. Authentication and current administrator permission are required;
non-admin returns 403. All reads are bound to the authenticated tenant. Current
model ACL changes do not reinterpret previously reported usage.

Both responses contain `data`, `total` (full filtered row count), `limit`
(null for models), `offset`, `meta`, and additive `summary`. The summary contains
the same six token counters as a row, aggregated in PostgreSQL across the full
filtered date/search window before pagination; it is not a current-page sum.
An empty set of recorded rows returns `summary: null`, while actual zero-valued
observations return zero counters. Missing summary from older APIs remains unknown
in the dashboard rather than being reconstructed from a page. User rows expose `user_id`, `work_id`,
`display_name` plus the four counters, `input_total`, `total_tokens`. Work ID is
populated only when the grouped Runs all carry company-login identity. A Run with
no user can have null `user_id`; it is not labelled a company employee. Model rows
expose `model` and the same six counters. Ordering is total tokens descending,
then user ID/model ascending. Search is a case-insensitive literal substring of
user ID or display name. Counts are aggregated in PostgreSQL before pagination.

`meta` contains `start_date`, `end_date`, `timezone: Asia/Shanghai`,
`collection_started_at` (first application of schema 2026.10.20.2), and `coverage`:
`status`, `reason`, `scopes`, `partial_scopes`. Coverage describes the full filtered
window, not just the page. Status is `reported_final`, `partial`, or `unavailable`;
response reasons are `not_collected`, `report_missing`, `baseline_unknown`,
`partial_report`, or null. `model_token_usage_coverage` stores the server's effective
coverage/reason after each newly accepted or rejected observation, keyed by
callback receipt. It is not copied from a rejected payload. Window summaries use
the last such receipt within that window for each scope; a next-day final callback
cannot promote the previous day's partial window. Old sequence replays do not
change this history; conflicts get a new partial receipt. Windows preceding collection and Runs without reports
are incomplete. Empty data never asserts zero consumption. A page beyond the end
retains full count and coverage. Known zero observations can produce zero rows.
Scopes without any model report contribute coverage but never fabricated model rows.

## Schema, privacy and compatibility disposition

Schema 2026.10.20.2 adds `model_token_usage_scopes`,
`model_token_usage_observations`, `model_token_usage_deltas`,
`model_token_usage_coverage` and their window indexes. Scope baseline JSON and
platform-bound provider session identity remain private.
The existing concurrent-index ledger version is unchanged. There is no backfill.
Scope/sequence/baseline/baseline JSON/provider session identity, normalized model counters, finite coverage reasons,
source receipt ID and server timestamps are private persistence fields. Users
are derived by joining the platform Run identity, never executor-provided IDs.
No prompts, storage paths, lease secrets, commands or raw SDK extras are stored.
`model_usage_snapshot` is forbidden by ordinary-user payload sanitization and is
not a public chat event or a terminal reconciliation field.

No superseded production surface is removed. Legacy Run token counters and cost
telemetry remain compatibility-owned by existing Run/monitor projections. They
are explicitly not a source for this page. The usage dashboard updates the existing frontend in place: a compact date/search
toolbar, full-window token summary and composition, and user ranking with actual-model
details. The previous plain ranking layout has no parallel route or compatibility
renderer. Existing user/model endpoints and pagination remain usable; clients may
ignore additive `summary`, and newer clients show unknown summary values when an
older API omits it. No billing, export or model ACL contract is replaced. Apply
schema before new API/Worker/executor binaries; old callbacks without snapshots
remain accepted and their missing usage remains unknown. Rollback binaries do not
require deleting the additive ledger. Do not rewrite historical usage.

## Owning checks

`test_model_token_usage_contract.py` covers DTO validation, independent token
coverage, restore baseline, zero/unknown, callback transaction/fence and public
privacy. `test_model_token_usage_routes.py` covers administrator/tenant/date/page
mapping. `test_model_token_usage_postgres.py` requires
`AI_PLATFORM_S0A_SCHEMA_TEST_DSN` and exercises delta idempotency/conflict/regression,
rollback, cross-attempt accounting, Beijing midnight, model ordering, search,
pagination, known zero/unknown and tenant isolation using the real schema. Additional
cases exercise cross-midnight effective coverage, overlapping and valid restored
scopes across Attempts/Runs, and the real callback route with actual PostgreSQL
batch receipt/delta writes and rollback. Only the synthetic authority/lease adapter
is replaced in that integration test; receipt creation/deduplication and transaction
commit/rollback are real. Extreme ISO dates on both HTTP endpoints return 422.
Run these through `tools/run_test_stage.py`; skips do not satisfy real-DB acceptance.
