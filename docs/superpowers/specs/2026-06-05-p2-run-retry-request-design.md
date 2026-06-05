# P2 Run Retry Request Design

## Purpose

Add the first write-side P2 lifecycle control after the read-only provenance,
readiness, resume-manifest, and checkpoint-audit snapshots. The slice exposes a
platform-controlled retry request for an authorized failed or dead-letter run.

This is not an autonomous retry scheduler. It is an owner-scoped user action
that creates a new queued run from the source run, preserves context and
checkpoint reuse intent, records retry-specific events/audit, and keeps the
existing tool, sandbox, and executor gates unchanged.

## Source Constraints

- PRD G3 requires queue lifecycle retry/dead-letter/cancel/idempotency before
  long tasks are treated as reliable.
- PRD G10 requires checkpoint, provenance, and artifact lineage to be
  auditable before broader multi-agent runtime work.
- The foundation roadmap currently marks retry as preview-only through
  `retry_runtime_not_enabled`.
- Guardrails require ordinary-user projection redaction, same-tenant Admin
  projection, no high-risk sandbox/tool expansion, focused tests, review, and
  211 runtime evidence.

## Contract

Add:

```text
POST /api/ai/runs/{run_id}/retry
```

Response:

```json
{
  "run_id": "run_new",
  "session_id": "ses_existing",
  "status": "queued",
  "queue_position": 1,
  "queue_insight": {}
}
```

Behavior:

- Requires `require_principal`.
- Uses `get_authorized_run` through repository retry creation, so only the
  source run owner in the same tenant can retry.
- Applies the same active-run admission gate as normal run creation before
  copying or enqueueing retry work.
- Allows only terminal retryable statuses: `failed`, `dead-letter`,
  `dead_letter`, and `dead-lettered`.
- Rejects `queued`, `running`, `succeeded`, and `cancelled` with
  `409 status_not_retryable`.
- Rejects repeated retry requests with `409 retry_already_active` when the
  same owner already has a queued or running run copied from the source run.
- Locks the authorized source run row inside the retry transaction before
  checking for an active retry, so concurrent requests against the same source
  serialize and the second request observes the first queued/running retry.
- Returns controlled `404` for stale or deleted source agent/skill references
  instead of leaking an internal server error.
- Creates a new queued run with `copied_from_run_id` set to the source run.
- Reuses the existing copy-run context, skill release, snapshot, queue payload,
  and seeded resume-step paths.
- Marks seeded step metadata with `seeded_from: retry_run` for retry-created
  runs and keeps `seeded_from: copy_run` for copy-created runs.
- Writes `retry_requested` on the source run and `run_retry_created` on the new
  run before enqueueing.
- Writes an audit log `run.retry` scoped to the source run.
- Does not start a retry scheduler, subagent dispatch, new sandbox behavior, or
  high-risk tool execution.

## Implementation Shape

Keep route code in `app/routes/runs.py`:

- Extract the existing copy-run queue preparation into a private helper that can
  serve both copy and retry.
- `copy_run` keeps its current behavior.
- New `retry_run` runs active-run admission, calls the helper with
  `source="retry_run"`, and returns the same `RunControlResponse` shape.

Keep repository code in `app/repositories.py`:

- Add optional `for_update=True` support to `get_authorized_run(...)`.
- Add `get_active_retry_for_source_run(...)` for same-owner queued/running
  retry idempotency.
- Add `retry_run_as_new_task(...)`.
- It locks the source run row, checks source run status and active same-source
  retry state, then calls the existing `copy_run_as_new_task(...)`.
- It records source/new-run events and audit after the new run row exists.

## Verification

Focused local tests:

- Retry creates a queued copied run, enqueues it, and records retry source/new
  run events plus audit.
- Retry uses active-run admission and rejects active-limit overflow before
  copying or enqueueing.
- Retry rejects active, succeeded, and cancelled statuses.
- Retry rejects repeated same-source queued/running retry requests with
  `retry_already_active`.
- Retry source authorization is performed with a source-row `FOR UPDATE` lock
  before active-retry lookup to close the concurrent double-click/race window.
- Retry returns 404 for stale source agent/skill references.
- Retry returns 404 for another user or missing source run.
- Retry-created seeded steps record `seeded_from: retry_run`.
- Existing copy-run tests keep passing.
- Source-authority docs tests keep passing.

211 smoke:

- Health is OK.
- OpenAPI contains `/api/ai/runs/{run_id}/retry`.
- Seeded failed source run can be retried by owner and returns queued new run.
- Seeded active run returns `409 status_not_retryable`.
- Repeated retry on the same source while the first retry is queued/running
  returns `409 retry_already_active`.
- Active-run admission overflow returns `409 user_active_run_limit_exceeded`.
- Other user receives 404.
- New run has `copied_from_run_id`, source/new retry events, audit log, and
  queue payload exists.
- Smoke rows are cleaned up.

## Self-Review

- No placeholder requirements remain.
- The slice is intentionally smaller than retry policy scheduling.
- The endpoint is write-side but does not bypass existing queue, skill,
  context, permission, or sandbox gates.
- The plan avoids exposing executor private payloads in user-facing
  projections.
