# ai-platform Capacity Baseline

Date: 2026-07-02

This document closes the first #21 baseline step without raising production
concurrency defaults. It records the current configured ceiling, the live Admin
Runtime signal path, and the load-test gates required before any production
profile increase.
GitHub issue #21 is currently closed, but the capacity-upgrade evidence gate
remains open until all recorded load-test gates have operator-reviewed evidence.

## Current Default Ceiling

Generate the current configured baseline from the repository root:

```powershell
python tools/capacity_baseline.py --format markdown
python tools/capacity_baseline.py --format json
python tools/capacity_load_plan.py --format markdown --base-url http://127.0.0.1:8020
python tools/capacity_load_plan.py --format json --base-url http://127.0.0.1:8020
python tools/capacity_evidence_snapshot.py --overview-json <admin-runtime-overview.json> --commit-sha <deployed-commit> --format markdown
python tools/capacity_evidence_snapshot.py --overview-json <admin-runtime-overview.json> --commit-sha <deployed-commit> --format json
python tools/capacity_gate_readiness.py --snapshot-json <capacity-evidence-snapshot.json> --format markdown
python tools/capacity_gate_readiness.py --snapshot-json <capacity-evidence-snapshot.json> --format json
python tools/capacity_profile_readiness.py --snapshot-json <capacity-evidence-snapshot.json> --format markdown
python tools/capacity_profile_readiness.py --snapshot-json <capacity-evidence-snapshot.json> --format json
python tools/capacity_runtime_evidence.py --base-url http://127.0.0.1:8020 --user-id codex-capacity-audit --tenant-id default --roles admin --commit-sha <deployed-commit> --runtime-profile <profile> --skip-maintenance-cleanup --format json
python tools/capacity_bounded_load_harness.py --base-url http://127.0.0.1:8020 --gate api_read_write_burst --requests 10 --concurrency 2 --format json
python tools/capacity_bounded_load_harness.py --base-url http://127.0.0.1:8020 --gate run_creation_burst_by_tenant_and_user --requests 10 --concurrency 2 --format json
python tools/capacity_bounded_load_harness.py --base-url http://127.0.0.1:8020 --gate worker_processing_throughput --requests 10 --concurrency 2 --format json
python tools/capacity_bounded_load_harness.py --base-url http://127.0.0.1:8020 --gate queue_depth_and_lease_latency --requests 10 --concurrency 2 --format json
python tools/capacity_bounded_load_harness.py --base-url http://127.0.0.1:8020 --gate cancel_retry_resume_under_load --requests 10 --concurrency 2 --format json
python tools/capacity_bounded_load_harness.py --base-url http://127.0.0.1:8020 --gate sandbox_lease_creation_under_load --requests 10 --concurrency 2 --format json
python tools/capacity_bounded_load_harness.py --base-url http://127.0.0.1:8020 --gate model_gateway_timeout_and_backpressure --requests 10 --concurrency 2 --format json
```

The scripts intentionally do not print DSNs, Redis URLs, model gateway URLs,
API keys, callback tokens, real `.env` values, raw queue keys, sandbox work
directories, storage keys, or executor private payloads.

Current defaults from `app/settings.py` and `deploy/ai-platform/.env.example`:

| Capacity term | Current default |
| --- | --- |
| API process count | single uvicorn process per API container |
| API request concurrency | not hard-limited by platform config; requires load-test evidence |
| Database pool | min 1, max 10, timeout 10 seconds, max waiting 100 |
| Active worker runs | 3 |
| Per-user active admission | 3 queued/running runs |
| Tenant queue processing quota | disabled by default (`0`) |
| User queue processing quota | disabled by default (`0`) |
| Queue lease scan limit | 50 |
| Queue insight scan limit | 500 |
| Sandbox provider | `fake` by default |
| Sandbox active containers | ephemeral 2, persistent 1 |
| Model gateway concurrency | disabled by default (`MODEL_GATEWAY_REQUEST_CONCURRENCY_LIMIT=0`) |
| Multi-agent worker dispatcher | disabled by default |

`MODEL_GATEWAY_REQUEST_CONCURRENCY_LIMIT` is a source-level capacity control
for future model-gateway backpressure profiles. The default `0` keeps the
platform-level model gateway request limit disabled and preserves the
`model_gateway_concurrency_unbounded_by_platform` warning. If an operator sets a
positive value, the Admin Runtime capacity projection reports that configured
limit as `configured_request_concurrency_limit`, keeps the enforced
`request_concurrency_limit` empty, and adds
`model_gateway_configured_limit_not_enforced` plus
`model_gateway_capacity_unproven_without_load_test`. It still does not answer
the safe maximum concurrency question, does not enforce model gateway
backpressure, and does not permit production default increases without recorded
load-test evidence.

The machine-readable contract for this baseline is
`ai-platform.model-gateway-backpressure-policy.v1`. It is contract-only:
`MODEL_GATEWAY_REQUEST_CONCURRENCY_LIMIT=0` disables the platform request
limit, Admin Runtime must continue to expose `capacity.limits.model_gateway`,
`backpressure.model_gateway`, and
`observability.error_categories`, and the required recorded load
gate remains `model_gateway_timeout_and_backpressure`. The contract records
`enforcement_status = not_implemented`,
`capacity_evidence = unproven_without_load_test`, and
`production_default_policy = do_not_raise_without_recorded_load_test_evidence`;
it does not raise production concurrency defaults, satisfy the recorded
capacity-evidence gate, or close G9.

Even when a deployment profile sets `SANDBOX_CONTAINER_PROVIDER=docker` and
reviewed G7 sandbox hardening evidence exists for a runtime subject, the
capacity baseline remains fail-closed until an approved G7 status-upgrade
decision and B3 recorded load/profile evidence are present. Docker-provider
posture alone does not raise defaults or close G7/B3.

The current configured default answer remains: current defaults execute about three
background Agent runs concurrently per shared worker capacity. The API can
accept more requests and the queue can hold more runs, but safe sustained
capacity is unproven without load-test evidence.

## Admin Runtime Signal Path

Admins consume the live operational projection at:

```text
GET /api/ai/admin/runtime/overview
```

The overview now exposes:

- `capacity`: configured limits and missing-capacity warnings from
  `ai-platform.capacity-baseline.v1`.
- `database_pool`: sanitized pool configuration and live pool stats.
- `queue.status` and `queue.tenant_insight`: queue depth, processing depth,
  worker heartbeats, queue sampling, and tenant/user throttling.
- `admission`: same-tenant active-run saturation.
- `backpressure`: normalized queue, active-run, DB-pool pressure reasons, and
  config-only model gateway limit/evidence status.
- `observability_readiness`: G9 runtime metrics, error taxonomy, quality,
  alert/export readiness domains and open gaps.

The migrated frontend now has an admin-only Settings section that reads this
same overview projection and surfaces capacity, backpressure, governance gaps,
model gateway limit status, and missing load-test evidence. This is an
operator visibility step only; it does not provide load-test proof and does
not raise any production default.

The projection is admin-only, same-tenant, and sanitized. Frontend capacity and
backpressure views must consume this projection rather than executor private
payloads, raw Redis keys, storage keys, sandbox work directories, raw `.env`
values, or secret-like data.

## Evidence Snapshot

After an operator captures the admin-only overview projection for a target
runtime, generate a secret-safe evidence snapshot:

```powershell
python tools/capacity_evidence_snapshot.py --overview-json .\admin-runtime-overview.json --commit-sha <deployed-commit> --format markdown
python tools/capacity_evidence_snapshot.py --overview-json .\admin-runtime-overview.json --commit-sha <deployed-commit> --format json
```

This snapshot extracts only allowlisted capacity, queue, admission,
backpressure, DB-pool, sandbox, and observability fields. It is designed to
bind live signals to a deployed commit while preserving the #21 rule that
load-test evidence is still `missing` until a real harness run records the
required gates. It does not read gateway secrets, send load, create runs, or
raise any default.

After generating a snapshot, operators can produce a gate verdict with:

```powershell
python tools/capacity_gate_readiness.py --snapshot-json .\capacity-evidence-snapshot.json --format markdown
python tools/capacity_gate_readiness.py --snapshot-json .\capacity-evidence-snapshot.json --format json
```

The verdict checks whether all required Admin Runtime sections were captured
and whether all seven load-test gates have recorded evidence. It is a
fail-closed verifier: missing sections or missing recorded gates keep
`production_default_decision =
do_not_raise_without_recorded_load_test_evidence`.

Operators can then map the gate verdict to the three requested deployment
profile classes without inventing concurrency numbers:

```powershell
python tools/capacity_profile_readiness.py --snapshot-json .\capacity-evidence-snapshot.json --format markdown
python tools/capacity_profile_readiness.py --snapshot-json .\capacity-evidence-snapshot.json --format json
python tools/capacity_profile_readiness.py --readiness-json .\capacity-gate-readiness.json --format json
```

The profile output uses schema
`ai-platform.capacity-profile-readiness.v1` and covers:

- `b3_10x4_sdk_subagents`
- `conservative_internal`
- `medium_team`
- `high_capacity_1t`

B3 operator-reviewed recorded snapshot source contract:
`tools/capacity_profile_readiness.py` emits
`ai-platform.capacity-operator-reviewed-recorded-snapshot-contract.v1` for
`b3_10x4_sdk_subagents`. This source contract only defines what an
operator-reviewed recorded snapshot must contain for the
10 sessions x peak 4 SDK subagents/session profile; it is not load evidence by
itself. The profile-evidence section must bind the snapshot to
`target_profile_id = b3_10x4_sdk_subagents`, use an allowlisted platform
runtime source such as `evidence_source = platform_runtime_profile`, record
`observed_concurrent_sessions >= 10`, record
`observed_peak_sdk_subagents_per_session >= 4`, and include a safe
`sdk_subagent_fanout_measurement_ref` artifact reference. It must also keep
the non-expansion flags
`production_concurrency_defaults_raised = false`,
`safe_concurrency_claimed = false`, and
`ordinary_user_platform_multi_run_orchestration_enabled = false`; otherwise
`tools/capacity_profile_readiness.py` keeps the B3 profile at
`blocked_missing_profile_evidence`. Historical snapshots may still import the
legacy alias `ordinary_user_multi_agent_enabled = false`; the readiness path
normalizes it to the canonical platform-level multi-run flag. This flag means
no ordinary-user platform-level multi-run orchestration exposure; it is not
evidence that B3 is a G8 product route. Required review evidence:

- `runtime_source_identity_and_image_labels`
- `tenant_user_skill_mix`
- `token_cost_ledger`
- `event_artifact_volume`
- `sandbox_pressure_and_cleanup`
- `latency_p50_p95_p99`
- `error_budget_and_dead_letters`
- `rollback_plan_and_stop_conditions`

Contract flags stay fail-closed: `does_not_raise_defaults = true`,
`does_not_claim_safe_concurrency = true`,
`does_not_enable_ordinary_user_platform_multi_run_orchestration = true`, and
`does_not_close_b3_gate = true`. This is source contract only; it does not raise production defaults,
does not close B3, and must not be used as ordinary-user platform-level multi-run
orchestration exposure evidence.

This is a fail-closed operator catalog. When load-test evidence is missing or
incomplete, every profile keeps
`production_default_decision =
do_not_raise_without_recorded_load_test_evidence`. If all gate evidence is
complete, the status advances only to `operator_review_required` and the
decision advances only to
`operator_review_required_before_default_change`; it still does not claim a safe
concurrency number and does not automatically raise API, worker, DB pool, Redis
queue, sandbox, model-gateway, SDK subagent fanout, or platform-level
multi-run orchestration exposure defaults.
Recorded gate names alone are not sufficient evidence. For each recorded gate,
the snapshot must include a per-gate evidence contract with all required
evidence keys, cleanup proof status, and stop-condition status. If any recorded
gate lacks that evidence packet, the verifier returns
`blocked_incomplete_load_test_evidence`, lists the gate under
`invalid_load_test_evidence`, and keeps the same fail-closed production
decision. Template placeholders such as `<commit_sha>`, `TODO`, `TBD`,
`placeholder`, `fill-me`, or `replace-me` are also treated as missing evidence;
operators must replace them with real artifact references or measured values
from the approved load run.

For a single read-only capture command, use:

```powershell
python tools/capacity_runtime_evidence.py --base-url http://10.56.0.211:8020 --user-id codex-capacity-audit --tenant-id default --roles admin --commit-sha <deployed-commit> --runtime-profile 211-current --skip-maintenance-cleanup --format json
```

This command fetches only
`GET /api/ai/admin/runtime/overview?include_maintenance_cleanup=false`, then
emits the sanitized evidence snapshot and fail-closed gate readiness verdict. It
does not print the raw overview, send load, create runs, mutate runtime state,
run sandbox/container maintenance cleanup, or raise any default. If a deployment
requires `X-AI-Gateway-Secret`, pass only the environment variable name with
`--gateway-secret-env`; the secret value is read from the environment and never
printed. The no-cleanup flag is the default-stack capture mode: the production
compose path intentionally does not mount the Docker socket into the API
container, so the capture request itself must not fail or run cleanup just
because Docker SDK access is unavailable from inside the API process. This does
not make unavailable container observation acceptable B3 evidence: if the
projection reports `sandbox.container_observation_degraded=true` or
`sandbox.list_runtime_containers_status` is not `available`, capacity readiness
still treats `sandbox` as a missing Admin Runtime evidence section and remains
fail-closed.

## Operator Load-Test Workflow

`tools/capacity_load_plan.py` now emits a machine-readable
`operator_workflow` block in addition to the dry-run scenario command
manifest. Each scenario also includes `recorded_gate_evidence_contract` with
schema `ai-platform.capacity-recorded-gate-evidence-contract.v1`. That contract
names the snapshot write path `load_test_evidence.gate_evidence.<gate>`, the
required evidence keys, accepted cleanup and stop-condition statuses, and the
rule that triggered stop conditions must be empty before operator review. The
contract is a recording template only. It does not raise production concurrency defaults.
The workflow is intentionally conservative:

1. Capture start runtime evidence with `tools/capacity_runtime_evidence.py`.
2. Confirm the start gate verdict before applying load.
3. Execute only an operator-approved bounded load harness for one selected
   scenario. `tools/capacity_load_plan.py` itself remains dry-run-only.
4. Capture end runtime evidence with `tools/capacity_runtime_evidence.py`.
5. Record cleanup proof for test tenants, queues, sandbox leases, and generated
   artifacts.
6. Build a sanitized recorded-gate evidence packet with
   `tools/capacity_recorded_gate_evidence_packet.py` from operator-reviewed
   measured values. Do not use bounded probe output as packet input.
7. Assemble an operator-reviewed recorded-gate snapshot with
   `tools/capacity_recorded_gate_snapshot.py`. When the same operator packet
   includes the B3 SDK subagent fanout measurement, pass it through
   `--profile-evidence-json` so the tool writes only
    `load_test_evidence.profile_evidence.b3_10x4_sdk_subagents`.
8. Generate the final fail-closed verdict with
    `tools/capacity_gate_readiness.py`.
9. For the all-gates B3 plan, build a sanitized B3 profile evidence packet with
   `tools/capacity_profile_evidence_packet.py`, then build all seven sanitized
   recorded-gate packets and assemble one
   `capacity-recorded-gate-batch-snapshot.json` with repeated
   `--recorded-gate-evidence-json` inputs. This reaches only
   `operator_review_required` when every gate packet and the B3 profile evidence
   packet are accepted; it still does not close B3.

Every workflow step carries `does_not_raise_defaults = true`. The only step
that requires real load is marked `requires_explicit_operator_execution = true`.
This gives operators a repeatable evidence chain for #21 without implying that
any current profile has been load-tested.

`tools/capacity_bounded_load_harness.py` is the first repository-owned
operator harness entrypoint for that real-load step. It emits schema
`ai-platform.capacity-bounded-load-harness.v1`, defaults to dry-run, and only
sends requests when `--execute` is paired with
`--operator-acknowledgement send-bounded-load-without-default-raise`. The
harness currently supports all seven #21 load-test gates as bounded read-only
probe entrypoints. The API gate probes `GET /api/ai/health` plus
`GET /api/ai/admin/runtime/overview?include_maintenance_cleanup=false`; the
other six gates probe the same Admin Runtime overview only, with maintenance
cleanup disabled. This lets operators observe admission, worker heartbeat,
queue depth, retry/cancel pressure, sandbox lease/container counts, DB-pool,
backpressure, observability, and model-gateway projection fields without
creating runs, sending model calls, reading gateway secrets, triggering
sandbox/container maintenance cleanup, or marking a gate recorded. For every
successful Admin Runtime overview response, the harness requires the seven
baseline sections `capacity`, `database_pool`, `queue`, `admission`,
`backpressure`, `sandbox`, and `observability`; missing sections trigger
`admin_runtime_projection_sections_missing` and return only section names and
counts, not the raw projection body. A degraded sandbox projection is also
treated as missing B3 sandbox evidence even when the `sandbox` object is present,
because the gate requires runtime container observation, not just a schema
placeholder. The model-gateway gate records only
observed model-gateway projection field paths,
and missing required projection fields trigger
`model_gateway_projection_fields_missing` instead of producing a successful
probe. The taxonomy requirement is the `observability.error_categories`
container itself, so a healthy runtime with zero model-gateway errors is not
treated as missing projection evidence.

Machine-readable doc check: the harness currently supports
`api_read_write_burst`, `run_creation_burst_by_tenant_and_user`,
`worker_processing_throughput`, `queue_depth_and_lease_latency`,
`cancel_retry_resume_under_load`, `sandbox_lease_creation_under_load`, and
`model_gateway_timeout_and_backpressure`.

The harness output is intentionally marked `probe_only_not_recorded`,
`does_not_raise_defaults = true`, and `does_not_mark_gate_recorded = true`. It
is not accepted by `tools/capacity_gate_readiness.py` as recorded gate evidence.
Operators must still convert approved measured results, cleanup proof, and
stop-condition status into the `load_test_evidence.gate_evidence.<gate>` shape
below before #21 can advance to operator review.

The final evidence snapshot should use this shape before operator review:

```json
{
  "load_test_evidence": {
    "status": "recorded",
    "required_gates": ["api_read_write_burst"],
    "recorded_gates": ["api_read_write_burst"],
    "gate_evidence": {
      "api_read_write_burst": {
        "evidence": {
          "commit_sha": "22dc9e61605d406f10669e4f91f4cb1a87e2094d",
          "api_worker_image_labels": "capacity-evidence/api-worker-labels.json",
          "cleanup_proof": "capacity-evidence/api-burst-cleanup.json"
        },
        "cleanup_proof_status": "recorded",
        "stop_condition_status": "passed",
        "triggered_stop_conditions": []
      }
    }
  }
}
```

The real gate evidence must include every item emitted by
`tools/capacity_load_plan.py` under `required_evidence` as non-empty
`gate_evidence.<gate>.evidence` values or artifact references. The shortened
JSON above only shows the contract shape. Do not submit the template shape with
placeholder values as recorded evidence; `tools/capacity_gate_readiness.py`
will keep that verdict at `blocked_incomplete_load_test_evidence`.

### 211 Snapshot Evidence - 2026-06-08

The current 211 API/worker runtime was checked after the G6 redaction-preview
deployment. API and worker both reported image
`ai-platform:f7c6b0d-g6-memory-redaction-preview` with
`org.opencontainers.image.revision=f7c6b0d9114748fa249acb88da6584851c48aa96`,
and `GET /api/ai/health` returned `{"status":"ok"}`.

An operator-captured Admin Runtime overview was converted with:

```powershell
python tools/capacity_evidence_snapshot.py --overview-json <211-admin-runtime-overview.json> --commit-sha f7c6b0d9114748fa249acb88da6584851c48aa96 --runtime-profile 211-current --format json
```

The resulting `ai-platform.capacity-evidence-snapshot.v1` snapshot keeps
`load_test_evidence.status = missing` and
`production_default_decision = do_not_raise_without_recorded_load_test_evidence`.
It recorded the following allowlisted live signals:

| Signal | 211 value |
| --- | --- |
| Queue depth | queued `0`, processing `0`, dead-letter `6` |
| Worker queue capacity | max active worker runs `3`, available worker slots `3`, processing saturated `false` |
| Queue quota config | tenant processing limit `0`, user processing limit `0` |
| DB pool | open `true`, max size `10`, max waiting `100`, waiting requests `0` |
| Admission | active runs `27`, active users `24`, saturated users `0`, per-user limit `3` |
| Sandbox | provider `fake`, running containers `0`, active leases `0`, released leases `21` |
| Backpressure reasons | `queued_behind_existing_work` |
| Capacity warnings | API request concurrency unbounded by platform; model gateway concurrency unbounded by platform; tenant/user queue quotas disabled; sandbox provider not production Docker; sandbox hardening evidence missing |

The snapshot leak scan found no DSN, Redis URL, object-storage key, sandbox
workdir, executor private payload, callback token, session secret, gateway
token, or raw storage-key markers. This is live observability evidence, not
load-test evidence; it does not answer the safe maximum concurrency question
and must not be used to raise production defaults.

### 211 Gate Readiness Verdict - 2026-06-08

After `tools/capacity_gate_readiness.py` landed, the same 211 Admin Runtime
overview path was captured through admin trusted headers and converted locally
with the pushed verifier:

```powershell
python tools/capacity_evidence_snapshot.py --overview-json <211-admin-runtime-overview.json> --commit-sha f7c6b0d9114748fa249acb88da6584851c48aa96 --runtime-profile 211-current --format json
python tools/capacity_gate_readiness.py --snapshot-json <211-capacity-evidence-snapshot.json> --format json
```

The verdict schema was `ai-platform.capacity-gate-readiness.v1` with status
`blocked_missing_load_test_evidence`. The Admin Runtime overview contained all
required sections: `capacity`, `database_pool`, `queue`, `admission`,
`backpressure`, `sandbox`, and `observability`. The missing recorded load-test
gates remained all seven required gates:

- `api_read_write_burst`
- `run_creation_burst_by_tenant_and_user`
- `worker_processing_throughput`
- `queue_depth_and_lease_latency`
- `cancel_retry_resume_under_load`
- `sandbox_lease_creation_under_load`
- `model_gateway_timeout_and_backpressure`

The gate verdict kept `production_default_decision =
do_not_raise_without_recorded_load_test_evidence`. The leak scan over the
generated snapshot and verdict found no DSN, Redis URL, raw storage key,
sandbox workdir, executor private payload, callback token, bearer token, API
key, or secret-like marker.

### 211 Runtime Evidence - 2026-06-08, commit `22dc9e6`

After the G9 taxonomy/readiness deployment, API and worker both ran image
`ai-platform:22dc9e6-g9-readiness-taxonomy` with
`org.opencontainers.image.revision =
22dc9e61605d406f10669e4f91f4cb1a87e2094d`. API health, frontend proxy health,
container-local compile, Admin Runtime admin access, ordinary-user HTTP 403,
and recent API/worker startup log checks all passed.

The read-only capacity runtime evidence command was run against the 211 API:

```powershell
python tools/capacity_runtime_evidence.py --base-url http://10.56.0.211:8020 --user-id codex-capacity-audit --tenant-id default --roles admin --commit-sha 22dc9e61605d406f10669e4f91f4cb1a87e2094d --runtime-profile 211-current --format json
```

The output schema was `ai-platform.capacity-runtime-evidence.v1`. The nested
snapshot schema was `ai-platform.capacity-evidence-snapshot.v1`, and
`snapshot.runtime_identity.commit_sha` matched
`22dc9e61605d406f10669e4f91f4cb1a87e2094d`.

The gate readiness schema was `ai-platform.capacity-gate-readiness.v1` with
status `blocked_missing_load_test_evidence`. The production default decision
remained `do_not_raise_without_recorded_load_test_evidence`, and all seven
load-test gates were still missing recorded evidence:

- `api_read_write_burst`
- `run_creation_burst_by_tenant_and_user`
- `worker_processing_throughput`
- `queue_depth_and_lease_latency`
- `cancel_retry_resume_under_load`
- `sandbox_lease_creation_under_load`
- `model_gateway_timeout_and_backpressure`

This evidence belongs to the closed #21 history, but the capacity-upgrade
evidence gate remains open. It verifies that current 211 runtime visibility and
fail-closed policy are deployed, but it still does not provide a safe maximum
concurrency number and must not be used to raise production defaults.

### 211 Runtime Evidence - 2026-06-08, commit `be03c95`

After the frontend build-provenance hardening deployment, API and worker both
ran image `ai-platform:be03c95-frontend-provenance` with
`org.opencontainers.image.revision =
be03c953e60489f1d27b8e6d1a0a770f11e48fb8`. API health, frontend proxy health,
container-local compile, Admin Runtime admin access, ordinary-user HTTP 403,
Admin Runtime projection leak scan, and recent API/worker startup log checks
all passed.

The read-only capacity runtime evidence command was run against the 211 API:

```powershell
python tools/capacity_runtime_evidence.py --base-url http://10.56.0.211:8020 --user-id codex-capacity-audit --tenant-id default --roles admin --commit-sha be03c953e60489f1d27b8e6d1a0a770f11e48fb8 --runtime-profile 211-current --format json
```

The output schema was `ai-platform.capacity-runtime-evidence.v1`. The nested
snapshot schema was `ai-platform.capacity-evidence-snapshot.v1`, and
`snapshot.runtime_identity.commit_sha` matched
`be03c953e60489f1d27b8e6d1a0a770f11e48fb8`.

The gate readiness schema was `ai-platform.capacity-gate-readiness.v1` with
status `blocked_missing_load_test_evidence`. The production default decision
remained `do_not_raise_without_recorded_load_test_evidence`, and all seven
load-test gates were still missing recorded evidence:

- `api_read_write_burst`
- `run_creation_burst_by_tenant_and_user`
- `worker_processing_throughput`
- `queue_depth_and_lease_latency`
- `cancel_retry_resume_under_load`
- `sandbox_lease_creation_under_load`
- `model_gateway_timeout_and_backpressure`

This follow-up evidence belongs to the closed #21 history and continues to fail
closed for production concurrency. The capacity-upgrade evidence gate remains
open. The observed 211 defaults still include single API and worker process
counts, DB pool max size `10`, active worker runs `3`, per-user active
admission `3`, tenant/user queue quotas disabled, model gateway concurrency
unbounded by platform config, and sandbox provider `fake`.

### 211 Runtime Evidence and Bounded Probe - 2026-06-08, commit `3d607c9`

After the G9 latency-percentile acceptance deployment, API and worker both ran
image `ai-platform:3d607c9-g9-latency-acceptance` with
`org.opencontainers.image.revision =
3d607c96b8d8e21f59461bd94cc4b64de1d49dd5`. API health, container-local
compile, Admin Runtime admin access, ordinary-user HTTP 403, Admin Runtime
projection leak scan, and recent API/worker startup log checks all passed.

The read-only capacity runtime evidence command was run against the 211 API:

```powershell
python tools/capacity_runtime_evidence.py --base-url http://10.56.0.211:8020 --user-id codex-capacity-audit --tenant-id default --roles admin --commit-sha 3d607c96b8d8e21f59461bd94cc4b64de1d49dd5 --runtime-profile 211-current --format json
```

The output schema was `ai-platform.capacity-runtime-evidence.v1`. The nested
snapshot schema was `ai-platform.capacity-evidence-snapshot.v1`, and
`snapshot.runtime_identity.commit_sha` matched
`3d607c96b8d8e21f59461bd94cc4b64de1d49dd5`.

The gate readiness schema was `ai-platform.capacity-gate-readiness.v1` with
status `blocked_missing_load_test_evidence`. The production default decision
remained `do_not_raise_without_recorded_load_test_evidence`, and all seven
load-test gates were still missing recorded evidence.

The repository-owned bounded read-only probe was also executed against 211:

```powershell
python tools/capacity_bounded_load_harness.py --base-url http://10.56.0.211:8020 --gate api_read_write_burst --requests 20 --concurrency 4 --execute --operator-acknowledgement send-bounded-load-without-default-raise --user-id codex-capacity-audit --tenant-id default --roles admin --format json
```

The probe output schema was `ai-platform.capacity-bounded-load-harness.v1`,
status `probe_completed_not_gate_evidence`, and `sent_requests = 20`. HTTP
status counts were `{"200": 20}`. Observed Admin Runtime sections were
`admission`, `backpressure`, `capacity`, `database_pool`, `observability`,
`queue`, `sandbox`, and `status`. Probe latency was:

- `min = 1.187 ms`
- `p50 = 72.864 ms`
- `p95 = 252.748 ms`
- `p99 = 283.726 ms`
- `max = 283.726 ms`

The probe stop-condition status was `passed` with no triggered stop conditions.
The output remained `load_test_evidence_status = probe_only_not_recorded` and
`does_not_mark_gate_recorded = true`, so it is not accepted by
`tools/capacity_gate_readiness.py` as recorded gate evidence. Recent API/worker
logs after the probe had no traceback, exception, error, timeout, failed import,
permission, entrypoint, pydantic, module-not-found, or syntax-error markers.

This evidence updates the capacity baseline to the latest 211 runtime and proves
the bounded read-only probe path is operational. It still does not satisfy the
recorded capacity-evidence gate, does not claim a safe maximum concurrency
number, and must not be used to raise production defaults.

### 211 Runtime Evidence - 2026-07-02, commit `ae6b7e5`

After the PR #296 G7/B3 label-repair rollout, API and worker both ran image
`ai-platform:ae6b7e5-g7-b3-label-repair-v1`, with source/runtime/OCI labels
and in-container source markers bound to
`ae6b7e52c656fd8296cf039834ce8d8559b01228`.

The read-only capacity runtime evidence command was run inside the 211 API
container against the local API route because the 211 host Python environment
does not currently provide the app dependencies needed by the repository tool:

```powershell
python tools/capacity_runtime_evidence.py --base-url http://127.0.0.1:8020 --user-id codex-capacity-audit --tenant-id default --roles admin --commit-sha ae6b7e52c656fd8296cf039834ce8d8559b01228 --runtime-profile 211-current-ae6b7e5 --format json
```

The output schema was `ai-platform.capacity-runtime-evidence.v1`. The nested
snapshot schema was `ai-platform.capacity-evidence-snapshot.v1`, and
`snapshot.runtime_identity.commit_sha` matched
`ae6b7e52c656fd8296cf039834ce8d8559b01228`. The Admin Runtime overview returned
HTTP `200`, and the gate readiness schema was
`ai-platform.capacity-gate-readiness.v1` with status
`blocked_missing_load_test_evidence`.

The Admin Runtime overview contained all required capacity sections:
`capacity`, `database_pool`, `queue`, `admission`, `backpressure`, `sandbox`,
and `observability`. The missing recorded load-test gates remained all seven
required gates:

- `api_read_write_burst`
- `run_creation_burst_by_tenant_and_user`
- `worker_processing_throughput`
- `queue_depth_and_lease_latency`
- `cancel_retry_resume_under_load`
- `sandbox_lease_creation_under_load`
- `model_gateway_timeout_and_backpressure`

The derived `ai-platform.capacity-profile-readiness.v1` result kept
`b3_10x4_sdk_subagents` blocked because no operator-reviewed profile evidence
was attached for `target_profile_id`, `evidence_source`,
`observed_concurrent_sessions`,
`observed_peak_sdk_subagents_per_session`,
`sdk_subagent_fanout_measurement_ref`,
`production_concurrency_defaults_raised`, `safe_concurrency_claimed`, or
`ordinary_user_platform_multi_run_orchestration_enabled`.

The same `ae6b7e5` runtime also ran a bounded read-only probe sweep across
all seven harness gates:

- `api_read_write_burst`
- `run_creation_burst_by_tenant_and_user`
- `worker_processing_throughput`
- `queue_depth_and_lease_latency`
- `cancel_retry_resume_under_load`
- `sandbox_lease_creation_under_load`
- `model_gateway_timeout_and_backpressure`

Each bounded probe emitted schema
`ai-platform.capacity-bounded-load-harness.v1`, status
`probe_completed_not_gate_evidence`,
`load_test_evidence_status = probe_only_not_recorded`,
`does_not_mark_gate_recorded = true`, `sent_requests = 10`, and
`stop_condition_status = passed`. The current verifier interpretation keeps
the runtime-evidence wrapper fail-closed with `missing_sections=[]` and
`status=blocked_missing_load_test_evidence`; all seven recorded gates and the
`b3_10x4_sdk_subagents` profile evidence remain missing.

The production default decision remained
`do_not_raise_without_recorded_load_test_evidence`. This `ae6b7e5` read-only
runtime evidence proves 211 Admin Runtime capacity visibility and fail-closed
policy for `ae6b7e5`; it still does not provide recorded B3 load-test evidence,
does not prove the 10 sessions x peak 4 SDK subagents/session profile, does not
claim a safe maximum concurrency number, does not raise production defaults,
and does not close B3.

After PR #297, 211 API/worker were later observed running
`ai-platform:4805031-g7-b3-post-297-label-repair-v2`, while the backend source
marker still read `ae6b7e5` and the frontend image was
`ai-platform-frontend:ba81a0b`. That observation does not change the B3
capacity conclusion: all seven operator-reviewed recorded load-test gates and
the `b3_10x4_sdk_subagents` profile evidence are still missing.

### 211 Runtime Evidence - 2026-07-02, PR #304 runtime subject `decf33a`

After the PR #304 follow-up rollout, a read-only 211 identity check observed
API and worker running `ai-platform:decf33a-g7-b3-post-300-followup-v1`, with
source/runtime/OCI labels and the 211 source marker bound to
`decf33a017e0b97e2a2992f80e3ccdc19152c1f4`. The frontend image observed at the
same time was `ai-platform-frontend:e2189d1`, and `/api/ai/health` returned
`{"status":"ok"}`.

The read-only capacity runtime evidence command was run inside the 211 API
container against the local API route:

```powershell
python tools/capacity_runtime_evidence.py --base-url http://127.0.0.1:8020 --user-id codex-capacity-audit --tenant-id default --roles admin --commit-sha decf33a017e0b97e2a2992f80e3ccdc19152c1f4 --runtime-profile g7-b3-post-300-runtime-only-v1 --skip-maintenance-cleanup --format json
```

The output schema was `ai-platform.capacity-runtime-evidence.v1`. The nested
snapshot schema was `ai-platform.capacity-evidence-snapshot.v1`, and
`snapshot.runtime_identity.commit_sha` matched
`decf33a017e0b97e2a2992f80e3ccdc19152c1f4`. The source capture used
`/api/ai/admin/runtime/overview?include_maintenance_cleanup=false`, returned
HTTP `200`, and contained all required Admin Runtime capacity sections:
`capacity`, `database_pool`, `queue`, `admission`, `backpressure`, `sandbox`,
and `observability`.

The reviewed, redacted repository evidence entry is
`docs/release-evidence/capacity-gate-readiness/decf33a017e0b97e2a2992f80e3ccdc19152c1f4/2026-07-02-211-capacity-runtime-readiness-decf33a.json`.
It summarizes capacity visibility and fail-closed readiness only; it is not a
raw runtime payload export and is not recorded B3 load evidence.

The gate readiness schema was `ai-platform.capacity-gate-readiness.v1` with
status `blocked_missing_load_test_evidence`. The production default decision
remained `do_not_raise_without_recorded_load_test_evidence`, and all seven
load-test gates were still missing recorded evidence:

- `api_read_write_burst`
- `run_creation_burst_by_tenant_and_user`
- `worker_processing_throughput`
- `queue_depth_and_lease_latency`
- `cancel_retry_resume_under_load`
- `sandbox_lease_creation_under_load`
- `model_gateway_timeout_and_backpressure`

The nested capacity snapshot still reported profile `unproven_default`.
`profile_evidence` was empty, so the `b3_10x4_sdk_subagents` profile remains
blocked until operator-reviewed profile evidence records the required 10
sessions x peak 4 SDK subagents/session measurement and the required
non-expansion flags.

This `decf33a` capture supersedes the earlier `4805031`
capacity-pending/HTTP-500 observation for the currently running `decf33a`
runtime subject only. PR #304 is now merged at
`a9c78efa812efe96b0366011a0c731cb11eb0099`, but this evidence still does not
prove current-main `211 verified` for that merge commit, does not provide
recorded B3 load-test evidence, does not claim a safe maximum concurrency
number, does not raise production defaults, and does not close B3.

### 211 Runtime Evidence - 2026-07-02, PR #305 merge commit `28676df`

After PR #305 merged, a read-only 211 identity check observed API and worker
running `ai-platform:28676df-g7-b3-current-main-runtime-only-v1`, with
source/runtime/OCI labels bound to
`28676df4abcbb7063211fceb4cc1701648c43d49`. The frontend image observed at the
same time was `ai-platform-frontend:e2189d1`. API health on
`http://127.0.0.1:8020/api/ai/health` and frontend proxy health on
`http://127.0.0.1:18001/api/ai/health` both returned `{"status":"ok"}`.
The 211 repo-local source marker still read
`decf33a017e0b97e2a2992f80e3ccdc19152c1f4`, so this is runtime-image
rollout evidence with a source-authority caveat, not G0 closure.

The read-only capacity runtime evidence command was run inside the 211 API
container against the local API route:

```powershell
python tools/capacity_runtime_evidence.py --base-url http://127.0.0.1:8020 --user-id codex-capacity-audit --tenant-id default --roles admin --commit-sha 28676df4abcbb7063211fceb4cc1701648c43d49 --runtime-profile g7-b3-current-main-runtime-only-v1 --skip-maintenance-cleanup --format json
```

The output schema was `ai-platform.capacity-runtime-evidence.v1`. The nested
snapshot schema was `ai-platform.capacity-evidence-snapshot.v1`, and
`snapshot.runtime_identity.commit_sha` matched
`28676df4abcbb7063211fceb4cc1701648c43d49`. The source capture used
`/api/ai/admin/runtime/overview?include_maintenance_cleanup=false` and returned
HTTP `200`.

The reviewed, redacted repository evidence entry is
`docs/release-evidence/capacity-gate-readiness/28676df4abcbb7063211fceb4cc1701648c43d49/2026-07-02-211-capacity-runtime-readiness-28676df.json`.
It summarizes capacity visibility and fail-closed readiness only; it is not a
raw runtime payload export and is not recorded B3 load evidence.

The gate readiness schema was `ai-platform.capacity-gate-readiness.v1` with
status `blocked_missing_admin_runtime_sections`. The Admin Runtime evidence
listed required sections `capacity`, `database_pool`, `queue`, `admission`,
`backpressure`, `sandbox`, and `observability`; the readiness result treated
`sandbox` as missing, so the capture is more conservative than the earlier
`decf33a` capacity visibility record. The production default decision remained
`do_not_raise_without_recorded_load_test_evidence`, and all seven load-test
gates were still missing recorded evidence:

- `api_read_write_burst`
- `run_creation_burst_by_tenant_and_user`
- `worker_processing_throughput`
- `queue_depth_and_lease_latency`
- `cancel_retry_resume_under_load`
- `sandbox_lease_creation_under_load`
- `model_gateway_timeout_and_backpressure`

`profile_evidence` was empty, so the `b3_10x4_sdk_subagents` profile remains
blocked until operator-reviewed profile evidence records the required 10
sessions x peak 4 SDK subagents/session measurement and the required
non-expansion flags.

A same-session G7 hardening verifier attempt for run
`g7-current-main-28676df-20260702130121` did not produce passing G7 evidence:
the generator summary recorded `No module named 'pydantic'` in the 211 host
Python path. That failed attempt is a verifier-environment blocker only. It is
not wrapped as reviewed G7 release evidence, does not close G7, and must not be
used as B3 load evidence.

After installing verifier-only dependencies in a 211 temp venv, the same
`28676df` verifier path progressed to the sandbox executor and exposed a second
runtime blocker: `/v1/tasks/execute` returned HTTP `500` because the executor
container ran with `cap_drop=["ALL"]` and could not create
`/workspace/runtime` inside a host-user-owned workspace mount. A patched-source
diagnostic run, `g7-current-main-28676df-workspace-user-fix-20260702135351`,
passed all eight verifier checks after the executor was launched as the
workspace owner. That diagnostic proves the root cause and fix direction only;
it is not reviewed deployed-runtime G7 evidence and does not close G7.

This `28676df` capture supersedes the earlier `decf33a` capacity visibility
record only for the currently running API/worker image identity. It still does
not provide recorded B3 load-test evidence, does not claim a safe maximum
concurrency number, does not raise production defaults, does not close B3, and
does not close G0 because the 211 repo-local source marker remains stale.

### Post-PR #306 Runtime Note - 2026-07-02, merge commit `9c669761`

After PR #306 merged at `9c669761bbb4bd719af64a341d361b7c3b3e380e`, a read-only
211 identity check observed the repo-local source marker and API/worker
source/runtime/OCI labels at `9c669761`, with API and worker running
`ai-platform:9c66976-g7-b3-workspace-owner-v1`. Direct API health on
`http://127.0.0.1:8020/api/ai/health` returned `{"status":"ok"}`, and the
frontend root on `http://127.0.0.1:18001/` returned HTTP `200`.

No reviewed B3 capacity runtime evidence entry has been recorded for
`9c669761`. At this historical slice, the latest reviewed B3 capacity entry was
the `28676df` visibility record above, with all seven recorded load-test gates
and `b3_10x4_sdk_subagents` profile evidence still missing. Newer historical
latest-status reading used the later `755e50e` visibility record below; current
status reading must use the later `61073b1` visibility record, which is also
fail-closed and not recorded load evidence. The earlier deployed G7
verifier diagnostic for `9c669761`, `g7-current-main-9c66976-20260702145801`,
recorded `executed_task=false`, `sandbox_provider=unknown`, and
`[Errno 13] Permission denied: '[redacted-path]'`; this is not B3 load evidence
and does not make G7 or B3 gate-closable. A later sudo-context explicit G7
verifier run, `g7-current-main-9c66976-sudo-20260702155816`, passed all eight
verifier checks and is wrapped in reviewed release evidence at
`docs/release-evidence/g7-sandbox/9c669761bbb4bd719af64a341d361b7c3b3e380e/2026-07-02-211-g7-sandbox-runtime-hardening-9c669761.json`.
After the live default executor image was rebound to the current 9c669761 image,
the 2026-07-03 live-default G7 run
`g7-live-env-hardening-9c669761-sudo-20260703091724` was wrapped at
`docs/release-evidence/g7-sandbox/9c669761bbb4bd719af64a341d361b7c3b3e380e/2026-07-03-211-g7-sandbox-live-env-hardening-9c669761.json`,
and same-subject Foundation Runtime concurrency evidence was recorded at
`docs/release-evidence/foundation-runtime-concurrency/9c669761bbb4bd719af64a341d361b7c3b3e380e-frc-g7-b3-20260703/2026-07-03-211-foundation-alpha-poc-9c669761-foundation-runtime-concurrency.json`.
Those G7/FRC records can support a G7
`candidate_evidence_requires_review` reading for `9c669761`, but all B3 recorded
load gates remain missing. The operator status-review artifact at
`docs/release-evidence/g7-status-review/9c669761bbb4bd719af64a341d361b7c3b3e380e/2026-07-03-211-g7-operator-status-review-9c669761.json`
records `status_upgrade_decision=not_approved_for_closure`, so G7 closure and
`211 verified` claims remain blocked.

### Post-PR #308 Runtime Note - 2026-07-03, merge commit `15903fd`

After PR #308 merged at `15903fdfe96ffcfba9daa1252741111017dcf832`, a later
label-clean 211 identity check observed the repo-local source marker at
`15903fd`, with API and worker running
`ai-platform:15903fd-g7-b3-label-clean-v2`. Canonical API/worker
source/runtime/OCI labels and legacy source alias labels bind to `15903fd`.
Direct API health on
`http://127.0.0.1:8020/api/ai/health`, frontend proxy health on
`http://127.0.0.1:18001/api/ai/health`, and frontend root on
`http://127.0.0.1:18001/` were healthy after the frontend container restart.

No reviewed B3 capacity runtime evidence entry has been recorded for
`15903fd`. At this historical slice, the latest reviewed B3 capacity entry was
the `28676df` visibility record above, with all seven recorded load-test gates
and `b3_10x4_sdk_subagents` profile evidence still missing. Newer historical
latest-status reading used the later `755e50e` visibility record below; current
status reading must use the later `61073b1` visibility record, which is also
fail-closed and not recorded load evidence. The 2026-07-03
label-clean live-default G7 run
`g7-live-env-hardening-15903fd-label-clean-sudo-20260703055828` is wrapped at
`docs/release-evidence/g7-sandbox/15903fdfe96ffcfba9daa1252741111017dcf832/2026-07-03-211-g7-sandbox-live-env-hardening-15903fd-label-clean.json`,
and same-subject Foundation Runtime concurrency evidence is recorded at
`docs/release-evidence/foundation-runtime-concurrency/15903fdfe96ffcfba9daa1252741111017dcf832-frc-g7-b3-20260703/2026-07-03-211-foundation-alpha-poc-15903fd-foundation-runtime-concurrency.json`.
Those records improve the current runtime evidence set, but the paired
label-clean operator status-review artifact at
`docs/release-evidence/g7-status-review/15903fdfe96ffcfba9daa1252741111017dcf832/2026-07-03-211-g7-operator-status-review-15903fd-label-clean.json`
records `status=candidate_evidence_requires_review`,
`g7_runtime_blocking_reasons=[]`, and
`status_upgrade_decision=not_approved_for_closure`. G7 closure, B3 closure,
`211 verified` claims remain blocked, and this does not constitute current G7/B3 closure evidence for the already-closed historical #164.

### Historical Runtime Note - 2026-07-03, commit `755e50e`

A later 211 readback observed the repo-local source marker at
`755e50ea2ad08c2d4218ae5d8cc612970b19e2a4`, with API and worker running the
dirty runtime-only local patch image
`ai-platform:755e50e-g7-b3-principal-userid-fix-v2`. Canonical API/worker
source/runtime/OCI labels and legacy source alias labels bind to `755e50e`;
`ai-platform.build-dirty=true`, rollout is
`g7-b3-755e50e-principal-userid-fix-v2`, and the live executor image remains
`ai-platform:755e50e-g7-b3-principal-userid-fix-v1`. Fresh 211 readback still
observed legacy in-container marker files at `9c669761` and `28676df`, so this
is not G0/source-authority closure or clean current-main `211 verified`
evidence. Fresh local Git readback on 2026-07-03 showed `origin/main` at
`1230dbc64a39805d6492a60c2688a2fed31ef3d9` after a frontend-only merge, so
`755e50e` is not latest clean `origin/main` runtime evidence. Direct API health
on `127.0.0.1:8020`, frontend proxy health, and frontend root were healthy.

The 2026-07-03 dirty-runtime v2 live-env G7 run
`g7-live-env-hardening-755e50e-principal-userid-fix-v2-container-20260703115120`
is wrapped at
`docs/release-evidence/g7-sandbox/755e50ea2ad08c2d4218ae5d8cc612970b19e2a4/2026-07-03-211-g7-sandbox-live-env-hardening-755e50e.json`.
Its verifier summary records all eight G7 runtime checks passing. Same-subject
FRC evidence is now recorded at
`docs/release-evidence/foundation-runtime-concurrency/755e50ea2ad08c2d4218ae5d8cc612970b19e2a4-frc-g7-b3-20260703/`; the readiness file reports
`verified_foundation_runtime_concurrency`, `verified=true`, `failures=[]`, 12
concurrent requests/runs/sessions across 2 tenants and 4 users, and passed
queue, sandbox, memory/context, artifact ACL, tool permission, skill snapshot,
and run playback checks. The earlier `/tmp/frc-755e50e-20260703T090109Z`
`queue_payload_invalid` attempt is retained only as superseded diagnostic
history.

The read-only capacity runtime evidence command was then run inside the 211 API
container against the same dirty-runtime v2 subject:

```powershell
python tools/capacity_runtime_evidence.py --base-url http://127.0.0.1:8020 --user-id codex-capacity-audit --tenant-id default --roles admin --commit-sha 755e50ea2ad08c2d4218ae5d8cc612970b19e2a4 --runtime-profile g7-b3-755e50e-principal-userid-fix-v2 --skip-maintenance-cleanup --format json
```

The redacted reviewed summary is recorded at
`docs/release-evidence/capacity-gate-readiness/755e50ea2ad08c2d4218ae5d8cc612970b19e2a4/2026-07-03-211-capacity-runtime-readiness-755e50e.json`.
It records Admin Runtime HTTP `200`, schema
`ai-platform.capacity-runtime-evidence.v1`, nested gate readiness
`blocked_missing_admin_runtime_sections`, observed sections `capacity`,
`database_pool`, `queue`, `admission`, `backpressure`, and `observability`, and
missing Admin Runtime capacity section `sandbox` because live container
observation was unavailable/degraded in the API-container no-cleanup capture.
All seven recorded load-test gates and `b3_10x4_sdk_subagents` profile evidence
are still missing. This is
visibility only; it is not a raw runtime payload export and is not recorded B3
load evidence. G7 closure, B3 closure, and clean current-main `211 verified` claims remain
blocked; this does not constitute current G7/B3 closure evidence for the
already-closed historical #164.

### Current Runtime Note - 2026-07-03, commit `61073b1`

A later clean current-main 211 rollout binds the repo-local source marker,
source snapshot, API/worker image labels, and API/worker in-container source
markers to
`61073b16a5b2c135e7ee467434ab39502ca3d194`, with API and worker running
`ai-platform:61073b1-g7-b3-clean-main-v1`, image ID
`sha256:59292f7687a1df372367e6cc3018b51d08661a94e13c1e91fbf6b8e37c113a0c`,
and `ai-platform.build-dirty=false`. The safe runtime env records
`SANDBOX_CONTAINER_PROVIDER=docker`,
`SANDBOX_EXECUTOR_IMAGE=ai-platform:61073b1-g7-b3-clean-main-v1`, and
`SANDBOX_EGRESS_POLICY_ENABLED=true`. Direct API health, frontend proxy health,
and frontend root HTTP remained healthy after rollout and image cleanup.

The clean current-main live-env G7 run
`g7-live-env-hardening-61073b1-clean-main-20260703161911` is wrapped at
`docs/release-evidence/g7-sandbox/61073b16a5b2c135e7ee467434ab39502ca3d194/2026-07-03-211-g7-sandbox-live-env-hardening-61073b1-clean-main.json`.
Its verifier summary records all eight G7 runtime checks passing. Same-subject
FRC evidence is now recorded at
`docs/release-evidence/foundation-runtime-concurrency/61073b16a5b2c135e7ee467434ab39502ca3d194-frc-g7-b3-20260703/`;
the readiness file reports `verified_foundation_runtime_concurrency`,
`verified=true`, `failures=[]`, 12 concurrent requests/runs/sessions across 2
tenants and 4 users, and passed queue, sandbox, memory/context, artifact ACL,
tool permission, skill snapshots, and run playback checks. This is clean
current-main G7 runtime and Foundation Runtime POC correctness evidence. The
paired operator status-review artifact at
`docs/release-evidence/g7-status-review/61073b16a5b2c135e7ee467434ab39502ca3d194/2026-07-03-211-g7-operator-status-review-61073b1-clean-main.json`
records `status=candidate_evidence_requires_review`,
`g7_runtime_blocking_reasons=[]`, and
`status_upgrade_decision=not_approved_for_closure`. This is not G7 closure
because no approved status upgrade exists and B3 recorded load/profile evidence
is still missing.

The read-only capacity runtime evidence command was then run inside the 211 API
container against the same clean current-main subject:

```powershell
python tools/capacity_runtime_evidence.py --base-url http://127.0.0.1:8020 --user-id codex-capacity-audit --tenant-id default --roles admin --commit-sha 61073b16a5b2c135e7ee467434ab39502ca3d194 --runtime-profile g7-b3-61073b1-clean-main-v1 --skip-maintenance-cleanup --format json
```

For a fresh 61073b1 refresh, operators must treat Admin Runtime as a protected
admin projection. A bare `curl` or tool run without the required gateway secret
can return HTTP `401` and is not evidence that the capacity projection is
unavailable. Use the same command shape with `--gateway-secret-env
AI_PLATFORM_GATEWAY_SECRET` when the deployed route requires
`X-AI-Gateway-Secret`; never print or commit the secret value. This refresh is
still visibility-only unless it is followed by approved load execution,
operator-reviewed measured values, cleanup proof, stop-condition evidence, all
seven recorded-gate packets, and the `b3_10x4_sdk_subagents` profile packet.

The redacted reviewed summary is recorded at
`docs/release-evidence/capacity-gate-readiness/61073b16a5b2c135e7ee467434ab39502ca3d194/2026-07-03-211-capacity-runtime-readiness-61073b1.json`.
It records Admin Runtime HTTP `200`, schema
`ai-platform.capacity-runtime-evidence.v1`, nested gate readiness
`blocked_missing_admin_runtime_sections`, observed sections `capacity`,
`database_pool`, `queue`, `admission`, `backpressure`, and `observability`, and
missing Admin Runtime capacity section `sandbox`. All seven recorded load-test
gates and `b3_10x4_sdk_subagents` profile evidence are still missing. This is
visibility only; it is not a raw runtime payload export and is not recorded B3
load evidence. G7 closure, B3 closure, Foundation Alpha completion, production
readiness, and `gate closable` claims remain blocked; this does not constitute
current G7/B3 closure evidence for the already-closed historical #164.

### Evidence Bundle Draft Tool

After operators capture start/end runtime evidence, the bounded probe JSON, and
cleanup proof, they can assemble a gap-first draft with:

```powershell
python tools/capacity_evidence_bundle.py --start-runtime-evidence-json capacity-runtime-evidence-start.json --runtime-evidence-json capacity-runtime-evidence-end.json --bounded-probe-json capacity-bounded-load-harness-api-read-write-burst.json --cleanup-proof-json capacity-cleanup-proof-api-read-write-burst.json --format markdown
```

The output schema is `ai-platform.capacity-evidence-bundle.v1`. It records the
target path `load_test_evidence.gate_evidence.api_read_write_burst`, observed
candidate fields, missing required fields, a start/end runtime window, cleanup
proof status, and a final readiness preview. The draft uses
`recorded_gate_evidence_draft.status = draft_not_recorded`, preserves
`probe_only_not_recorded`, and keeps `does_not_mark_gate_recorded = true`; it is
not accepted by `tools/capacity_gate_readiness.py` as recorded gate evidence
and does not raise production concurrency defaults.

The optional cleanup proof input is only accepted when it uses schema
`ai-platform.capacity-cleanup-proof.v1`, matches the selected gate, includes a
safe relative `evidence_ref`, and explicitly verifies
`test_tenants_removed`, `queued_payloads_removed`, `sandbox_leases_released`,
`temporary_artifacts_removed`, and `generated_documents_removed`. Raw storage
keys, executor private payloads, sandbox workdirs, secret-like values, URLs,
absolute paths, path traversal, and raw/private path segments are rejected from
the bundle.

`tools/capacity_load_plan.py` now includes this as the
`assemble_evidence_bundle_draft` operator workflow step after cleanup proof is
recorded. The step is a planning/readiness aid only: it points at
`capacity-runtime-evidence-start.json`, `capacity-runtime-evidence-end.json`,
`capacity-bounded-load-harness-api-read-write-burst.json`, and
`capacity-cleanup-proof-api-read-write-burst.json`, then produces
`capacity-evidence-bundle-api-read-write-burst.md`. Recorded load-test
artifacts, final gate readiness, and operator review remain separate required
work.

### Recorded Gate Packet And Snapshot Assembly Tools

After an operator has reviewed measured artifacts into compact per-gate values,
build a sanitized packet first:

```powershell
python tools/capacity_recorded_gate_evidence_packet.py --gate api_read_write_burst --evidence-json capacity-operator-reviewed-evidence-values-api-read-write-burst.json --cleanup-proof-status verified --stop-condition-status passed --format json > capacity-recorded-gate-evidence-api-read-write-burst.json
```

The packet builder output schema is
`ai-platform.capacity-recorded-gate-evidence-packet-result.v1`. The nested
`packet` value uses schema `ai-platform.capacity-recorded-gate-evidence.v1`
and is the input for `tools/capacity_recorded_gate_snapshot.py`. The builder
rejects `ai-platform.capacity-bounded-load-harness.v1`,
`probe_only_not_recorded`, or `does_not_mark_gate_recorded = true` inputs, so
bounded probe output cannot be promoted into recorded gate evidence by renaming
the file.

After the SDK subagent fanout measurement has been reviewed, build the B3
profile evidence packet separately:

```powershell
python tools/capacity_profile_evidence_packet.py --evidence-json capacity-operator-reviewed-profile-values-b3-10x4-sdk-subagents.json --format json > capacity-profile-evidence-b3-10x4-sdk-subagents.json
```

The profile packet builder output schema is
`ai-platform.capacity-profile-evidence-packet-result.v1`. Its nested `packet`
is exactly the value passed to `tools/capacity_recorded_gate_snapshot.py` via
`--profile-evidence-json`. It validates the `b3_10x4_sdk_subagents` target id,
10 observed concurrent sessions, peak 4 SDK subagents per session, safe fanout
measurement reference, and the non-expansion flags before any batch snapshot can
reach `operator_review_required`.

After the packet is ready, assemble the recorded-gate snapshot with:

```powershell
python tools/capacity_recorded_gate_snapshot.py --runtime-evidence-json capacity-runtime-evidence-end.json --recorded-gate-evidence-json capacity-recorded-gate-evidence-api-read-write-burst.json --profile-evidence-json capacity-profile-evidence-b3-10x4-sdk-subagents.json --gate api_read_write_burst --format json
```

When all seven operator-reviewed gate packets are available, submit them in one
fail-closed batch command instead of assembling seven intermediate snapshots:

```powershell
python tools/capacity_recorded_gate_snapshot.py --runtime-evidence-json capacity-runtime-evidence-end.json --recorded-gate-evidence-json capacity-recorded-gate-evidence-api-read-write-burst.json --recorded-gate-evidence-json capacity-recorded-gate-evidence-run-creation-burst-by-tenant-and-user.json --recorded-gate-evidence-json capacity-recorded-gate-evidence-worker-processing-throughput.json --recorded-gate-evidence-json capacity-recorded-gate-evidence-queue-depth-and-lease-latency.json --recorded-gate-evidence-json capacity-recorded-gate-evidence-cancel-retry-resume-under-load.json --recorded-gate-evidence-json capacity-recorded-gate-evidence-sandbox-lease-creation-under-load.json --recorded-gate-evidence-json capacity-recorded-gate-evidence-model-gateway-timeout-and-backpressure.json --profile-evidence-json capacity-profile-evidence-b3-10x4-sdk-subagents.json --format json
```

The snapshot input packet schema is
`ai-platform.capacity-recorded-gate-evidence.v1`. The output schema is
`ai-platform.capacity-recorded-gate-snapshot.v1`. These steps are recorded in
the generated operator workflow as `build_recorded_gate_evidence_packet` and
`assemble_recorded_gate_snapshot`. When `tools/capacity_load_plan.py` is run
without a single `--scenario`, the generated workflow also includes
`build_b3_profile_evidence_packet`, `build_all_recorded_gate_evidence_packets`, and
`assemble_recorded_gate_batch_snapshot` so operators have a copyable all-seven
gate assembly path for B3.

The recorded-gate tool only accepts explicit operator-reviewed values for all
required gate evidence fields. Each field must be a safe relative artifact
reference or a scalar measured value, and the packet must carry
`does_not_raise_defaults = true`, accepted cleanup proof status, accepted
stop-condition status, and no triggered stop conditions. A direct
`ai-platform.capacity-recorded-gate-evidence.v1` packet is still rejected if it
carries bounded-probe markers such as
`load_test_evidence_status = probe_only_not_recorded` or
`does_not_mark_gate_recorded = true`, even when the nested field values look
complete. When `--profile-evidence-json` is supplied, that separate packet is
sanitized through the B3 profile contract and can only populate
`load_test_evidence.profile_evidence.b3_10x4_sdk_subagents`; it does not mark
any gate recorded. URLs, absolute paths, path traversal, raw/private path
segments, secret-like markers, raw storage keys, sandbox workdirs, and executor
private payloads are rejected without echoing the unsafe value.

This tool does not turn `probe_only_not_recorded` bounded probe output into
recorded gate evidence by itself. It only merges a reviewed evidence packet
into a sanitized capacity evidence snapshot and immediately returns a
fail-closed readiness preview. If only one gate is recorded, the preview still
keeps the remaining gates missing and preserves
`production_default_decision =
do_not_raise_without_recorded_load_test_evidence`. If all seven gates are
recorded and B3 profile evidence is accepted, the batch output records
`status=recorded_gate_batch_input_accepted` and
`readiness.status=ready_for_operator_review`.
`tools/capacity_profile_readiness.py` can then advance only to
`operator_review_required`; it still does not close B3 or raise production
defaults.

## Required Load-Test Gates

Generate the repeatable command manifest for a target deployment profile:

```powershell
python tools/capacity_load_plan.py --format markdown --base-url http://10.56.0.211:8020 --tenants 3 --users-per-tenant 5 --runs-per-user 2 --duration-seconds 300
```

The load-plan tool is intentionally dry-run only. It records the scenario
parameters, evidence fields, stop conditions, and cleanup policy operators must
follow when they run a real harness on 211 or another Docker-capable/internal
host. It does not create runs, issue traffic bursts, mutate runtime state, or
raise production defaults.

Each generated scenario now also names the Admin Runtime sections that must be
captured during the run: `capacity`, `database_pool`, `queue`, `admission`,
`backpressure`, `sandbox`, and `observability`. `sandbox` is required even when
the current provider is `fake`, because #21 still needs lease/container counts,
cleanup evidence, and later Docker-provider hardening proof before any
production concurrency increase.

Do not raise production concurrency defaults until these gates have recorded
evidence for the target deployment profile:

1. API read/write request burst.
2. Run creation burst across N tenants and M users.
3. Worker processing throughput at expected active-run count.
4. Queue depth and lease latency at large queued depth.
5. Cancel/retry/resume behavior under load.
6. Sandbox lease creation, renewal, cleanup, and cold-start latency under load.
7. Model gateway timeout, retry, and backpressure behavior.

Each run should capture:

- git commit SHA and deployed image labels;
- API/worker process and container counts;
- configured DB pool, Redis queue, admission, worker, sandbox, and model
  gateway settings;
- peak and sustained queue depths;
- active worker runs, active users, active tenants, and saturated users;
- DB pool waiting and saturation;
- p50/p95/p99 latency for API create, queue lease, worker execution, model
  call, sandbox start, artifact write, cancel, retry, and resume;
- error taxonomy category counts, raw error-type counts after redaction, and
  dead-letter counts;
- cleanup proof for test tenants, runs, queue payloads, sandbox leases, and
  temporary artifacts.

## Guardrails

- Do not treat server memory size as capacity proof.
- Do not raise `MAX_ACTIVE_WORKER_RUNS`, `MAX_ACTIVE_RUNS_PER_USER`, DB pool
  size, queue tenant/user quotas, sandbox container limits, or model gateway
  concurrency without recorded load-test evidence.
- Do not enable ordinary-user platform-level multi-run orchestration as a
  capacity test shortcut. SDK subagent fanout capacity must be measured inside
  governed platform runs.
- Do not use Docker compose one-command startup as the current #21 gate.
- Run Docker and 211 runtime smoke only on Docker-capable hosts; this Windows
  workstation should use repository-native checks.
