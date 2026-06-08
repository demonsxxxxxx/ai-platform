# ai-platform Capacity Baseline

Date: 2026-06-07

This document closes the first #21 baseline step without raising production
concurrency defaults. It records the current configured ceiling, the live Admin
Runtime signal path, and the load-test gates required before any production
profile increase.

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
python tools/capacity_runtime_evidence.py --base-url http://127.0.0.1:8020 --user-id codex-capacity-audit --tenant-id default --roles admin --commit-sha <deployed-commit> --runtime-profile <profile> --format json
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
| Model gateway concurrency | no platform-level request limit yet |
| Multi-agent worker dispatcher | disabled by default |

Even if a deployment profile sets `SANDBOX_CONTAINER_PROVIDER=docker`, the
baseline must still warn that sandbox hardening evidence is missing until G7
records provider hardening, egress/quota, orphan-cleanup, and load-test proof.

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
- `backpressure`: normalized queue, active-run, and DB-pool pressure reasons.

The migrated frontend now has an admin-only Settings section that reads this
same overview projection and surfaces capacity, backpressure, governance gaps,
and missing load-test evidence. This is an operator visibility step only; it
does not provide load-test proof and does not raise any production default.

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

For a single read-only capture command, use:

```powershell
python tools/capacity_runtime_evidence.py --base-url http://10.56.0.211:8020 --user-id codex-capacity-audit --tenant-id default --roles admin --commit-sha <deployed-commit> --runtime-profile 211-current --format json
```

This command fetches only `GET /api/ai/admin/runtime/overview`, then emits the
sanitized evidence snapshot and fail-closed gate readiness verdict. It does not
print the raw overview, send load, create runs, mutate runtime state, or raise
any default. If a deployment requires `X-AI-Gateway-Secret`, pass only the
environment variable name with `--gateway-secret-env`; the secret value is read
from the environment and never printed.

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
- error taxonomy counts and dead-letter counts;
- cleanup proof for test tenants, runs, queue payloads, sandbox leases, and
  temporary artifacts.

## Guardrails

- Do not treat server memory size as capacity proof.
- Do not raise `MAX_ACTIVE_WORKER_RUNS`, `MAX_ACTIVE_RUNS_PER_USER`, DB pool
  size, queue tenant/user quotas, sandbox container limits, or model gateway
  concurrency without recorded load-test evidence.
- Do not enable multi-agent fanout for ordinary users as a capacity test
  shortcut.
- Do not use Docker compose one-command startup as the current #21 gate.
- Run Docker and 211 runtime smoke only on Docker-capable hosts; this Windows
  workstation should use repository-native checks.
