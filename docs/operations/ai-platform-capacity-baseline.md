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
