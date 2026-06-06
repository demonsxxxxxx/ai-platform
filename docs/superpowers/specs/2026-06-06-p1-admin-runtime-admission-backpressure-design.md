# P1 Admin Runtime Admission Backpressure Design

## Goal

Extend the existing admin-only runtime overview with a same-tenant admission
and backpressure snapshot. Operators should be able to see whether run creation
is blocked by per-user active-run limits, queue worker capacity, tenant/user
queue quota, or DB pool waiting pressure without reading executor private
payloads or raw queue messages.

This is a P1 Admin Runtime / Observability hardening slice. It does not add a
new frontend entry, does not change admission policy, and does not start Long
Task / Multi-Agent Runtime work.

## Source Constraints

Use the current `main`, the final PRD, foundation roadmap, repository
guardrails, current code, and fresh 211 evidence as authority. The local
workstation does not run Docker; Docker/compose/runtime smoke remains 211-only.

## Current State

`GET /api/ai/admin/runtime/overview` already returns same-tenant sections for:

- `queue.status` and admin `queue.tenant_insight`;
- `runs` status aggregates and recent redacted failures;
- `sandbox` container and lease counts;
- `observability` latency/token/cost/error/artifact/event aggregates;
- `database_pool` allowlisted config and numeric stats.

The gap is that active-run admission is now serialized, but Admin Runtime does
not summarize how close users are to `max_active_runs_per_user`, and the queue
/ pool pressure signals are not normalized into an operator-friendly
backpressure section.

## API Contract

Extend:

```text
GET /api/ai/admin/runtime/overview
```

Add:

```json
{
  "admission": {
    "policy_active": true,
    "max_active_runs_per_user": 3,
    "active_runs": 4,
    "active_users": 2,
    "saturated_users": 1,
    "top_users": [
      {"user_id": "user-a", "active": 3, "saturated": true}
    ]
  },
  "backpressure": {
    "reasons": ["active_run_limit_saturated", "workers_busy"],
    "queue": {
      "reason": "workers_busy",
      "worker_capacity": {
        "max_active_worker_runs": 3,
        "processing_saturated": false,
        "available_worker_slots": 1
      },
      "quota": {
        "tenant_processing_limit": 2,
        "tenant_processing_saturated": false,
        "user_processing_limit": 1,
        "saturated_users": 1
      },
      "sample": {
        "queued_scan_limit": 500,
        "queued_sampled": 12,
        "queued_sample_complete": true
      }
    },
    "database_pool": {
      "open": true,
      "requests_waiting": 0,
      "max_waiting": 100,
      "waiting_saturated": false
    }
  }
}
```

Field rules:

- `admission` comes from current same-tenant `runs` rows with statuses
  `queued` and `running`.
- `top_users` is admin-only and same-tenant. It includes only user id, active
  count, and saturation boolean.
- `backpressure.reasons` is deterministic and only contains public-safe enum
  strings. It can include `active_run_limit_saturated`, queue insight reasons,
  `queue_tenant_quota_saturated`, `queue_user_quota_saturated`,
  `worker_capacity_saturated`, `database_pool_waiting`, and
  `database_pool_waiting_saturated`.
- `queue` is derived from existing admin `get_queue_insight(...,
  include_user_breakdown=True)` output. It must not expose raw Redis keys or
  raw queue payloads in the new backpressure section.
- `database_pool` is derived from already sanitized pool status and includes
  only numeric allowlisted stats.

## Repository Helper

Add:

```python
async def get_admin_runtime_admission_summary(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    limit: int,
    top_user_limit: int = 10,
) -> dict[str, Any]:
    ...
```

Behavior:

- `limit <= 0`: policy is inactive; still return same-tenant active run/user
  counts, with no saturated users.
- Count only `queued` and `running`.
- Group by same-tenant `user_id`; ignore null `user_id` in `top_users`.
- Return top users ordered by active count descending then user id ascending.
- Do not return session ids, run ids, agent ids, skill ids, input payloads, or
  errors.

## Route Composition

`admin_runtime_overview` should fetch the new repository summary in the same
tenant-scoped DB transaction used for existing run/observability summaries.
The route then builds:

- `admission` from the repository helper and current settings.
- `backpressure` from admission, queue insight, and sanitized DB pool status.

Keep fail-closed behavior: if Redis queue inspection fails, the overview still
fails rather than returning partial success. Provider cleanup and sandbox
runtime cleanup behavior stays unchanged.

## Testing

Focused tests must cover:

- Repository admission summary scopes by tenant, counts queued/running only,
  honors disabled limits, and sorts top users deterministically.
- Admin overview includes `admission` and `backpressure`.
- Backpressure reasons include active-run saturation, queue reason, queue quota
  saturation, worker capacity saturation, and DB pool waiting states when
  present.
- Backpressure output does not include raw Redis keys, raw queue payload,
  storage keys, runtime paths, executor private payload, or secret-like fields.
- Existing non-admin and cleanup fail-closed tests remain unchanged.

## 211 Smoke

After local tests, review, merge, and deployment:

- Verify API/frontend health.
- Verify API/worker image labels and source markers.
- Call `/api/ai/admin/runtime/overview` as admin and confirm `admission`,
  `backpressure`, `database_pool`, and `queue` are present.
- Confirm ordinary user receives `403`.
- Confirm response text does not contain secret-like markers, raw Redis key
  names from the new backpressure section, storage keys, runtime private
  payloads, or sandbox work directories.

## Out Of Scope

- No schema migration.
- No new public user projection.
- No frontend page work in this slice.
- No long-task, checkpoint, subagent, or multi-agent scheduling changes.
- No policy changes to `max_active_runs_per_user` or queue quotas.
