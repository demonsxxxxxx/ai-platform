# P1 Admin Runtime Overview Snapshot Design

## Goal

Build the first P1 operational surface for ai-platform by adding an admin-only
runtime overview contract. The overview aggregates existing platform facts into
one same-tenant snapshot so operators can see queue pressure, run status,
sandbox lease/container state, and basic observability signals without reading
private executor payloads.

This is a foundation-hardening and Agent Frontend V1 support slice. It is not
the full Observability / Quality dashboard and it does not start Long Task /
Multi-Agent Runtime work.

## Source Constraints

Use these sources as the implementation authority:

- Current `main` branch and feature branch `codex/p1-admin-runtime-overview`.
- `docs/superpowers/specs/2026-05-29-ai-platform-final-product-prd.md`.
- `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`.
- `docs/agent-rules/ai-platform-guardrails.md`.
- Current backend code and focused tests.
- Fresh 211 runtime evidence only after local implementation and review.

The local workstation does not support Docker. Local validation uses Python
compile and pytest with `--basetemp .pytest-tmp`. Docker compose, image build,
restart, and runtime smoke validation happen only on 211 or another
Docker-capable host.

## API Contract

Add:

```text
GET /api/ai/admin/runtime/overview
```

Access rules:

- Requires `require_principal`.
- Requires `is_ai_admin(principal)`.
- Non-admin callers receive `403` with `not_ai_admin`.
- All database queries and provider calls are scoped to `principal.tenant_id`.

Response shape:

```json
{
  "tenant_id": "default",
  "queue": {
    "status": {},
    "tenant_insight": {}
  },
  "runs": {
    "total": 0,
    "by_status": {},
    "active": 0,
    "terminal": 0,
    "recent_failures": []
  },
  "sandbox": {
    "containers": {
      "total": 0,
      "running": 0,
      "by_status": {},
      "ephemeral_running": 0,
      "persistent_running": 0
    },
    "leases": {
      "active": 0,
      "released": 0,
      "expired": 0,
      "history_included": false
    }
  },
  "observability": {
    "event_count": 0,
    "artifact_count": 0,
    "error_count": 0,
    "error_types": {},
    "latency_ms": {
      "avg": null,
      "max": null
    },
    "token_counts": {
      "input": 0,
      "output": 0,
      "total": 0
    },
    "estimated_cost_minor": 0
  }
}
```

The exact contract can add fields when tests pin them, but it must not expose
raw skill ids as routing bypasses, storage keys, runtime paths, command
fingerprints, lease private payloads, request payloads, decision payloads, or
secret-like values.

## Data Flow

1. `app.routes.admin_runtime.admin_runtime_overview` validates the admin
   principal.
2. It reads queue facts from existing `get_queue_status()` and
   `get_queue_insight(principal.tenant_id)`.
3. It reads sandbox facts through the same cleanup and projection path used by
   `/admin/runtime/containers`, including provider orphan cleanup and expired
   lease cleanup before projection.
4. It reads run and observability aggregates from repository helpers. These
   helpers query only the current tenant and return already-summarized values.
5. The route assembles a single response model from sanitized summaries.

## Repository Helpers

Add focused helpers rather than expanding large route functions with SQL:

- `get_admin_runtime_run_summary(conn, *, tenant_id: str, limit: int = 10)`
  returns run counts, status counts, active/terminal counts, and recent
  redacted failures.
- `get_admin_runtime_observability_summary(conn, *, tenant_id: str)` returns
  event count, artifact count, error count, error type counts, latency summary,
  token totals, and estimated cost totals.

The helpers aggregate from existing tables and columns:

- `runs.status`, `runs.error_code`, `runs.error_message`,
  `runs.latency_ms`, token columns, and `estimated_cost_minor`.
- `run_events.error_code` and `run_events.latency_ms` for event/error/latency
  signals. Do not add `run_events` terminal token or cost values on top of
  `runs` terminal totals; the worker records the same terminal observability
  on both surfaces.
- `artifacts.id` for artifact count.

Any free-text error fields must pass through existing public sanitization before
they enter the response.

## Error Handling

- Provider cleanup failure remains fail-closed with
  `500 sandbox_provider_cleanup_failed`.
- Sandbox runtime cleanup failure remains fail-closed with
  `500 sandbox_runtime_cleanup_failed`.
- Repository contract conflicts return a server error with the existing
  repository conflict detail only when the detail is already public-safe.
- Queue Redis failures are not masked as successful overview data.

## Testing

Focused tests should cover:

- Non-admin callers cannot access `/admin/runtime/overview`.
- The route returns queue, run, sandbox, and observability sections for an
  admin caller.
- The route calls repository helpers with `principal.tenant_id`.
- Provider orphan cleanup and expired sandbox lease cleanup run before sandbox
  projection.
- Provider cleanup failure and sandbox cleanup failure fail closed.
- Recent failures and aggregate error fields do not leak secret-like tokens or
  runtime paths.
- Repository aggregate helpers generate tenant-scoped SQL and coerce nulls to
  stable numeric defaults.

Local verification before commit:

```powershell
python -m compileall -q app tools scripts
python -m pytest tests/test_admin_runtime_routes.py tests/test_repositories.py -q --basetemp .pytest-tmp
python -m pytest -q --basetemp .pytest-tmp
```

211 verification after review and deployment:

- API health returns `200`.
- `GET /api/ai/admin/runtime/overview` returns `200` for an admin principal.
- Non-admin overview access returns `403`.
- Response contains no real `.env` values, runtime private payloads, storage
  keys, or sandbox work directories.

## Out Of Scope

- No new frontend entry.
- No full Observability / Quality dashboard.
- No quality score or eval/golden-set system.
- No Long Task / Multi-Agent Runtime implementation.
- No Docker validation on the local Windows workstation.
