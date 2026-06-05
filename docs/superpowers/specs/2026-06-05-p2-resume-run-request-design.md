# P2 Resume Run Request Design

## Purpose

Add a write-side P2 lifecycle control for explicit resume requests after the
read-only readiness, resume-manifest, checkpoint-audit, retry, and checkpoint
lineage slices. The endpoint lets the source run owner create a queued resume
run only when the source is non-active and has reusable checkpoint output.

This is not an autonomous scheduler and it does not open new sandbox or tool
behavior. It reuses the existing platform-controlled copy/context/queue path,
but marks the new request with resume-specific source, events, and audit
evidence.

## Source Constraints

- PRD G10 requires checkpoint, resume, provenance, artifact, and event semantics
  to be auditable before broader long-task and multi-agent runtime work.
- The foundation roadmap already has read-only resume readiness, resume
  manifest, checkpoint audit, retry request, and checkpoint lineage slices.
- Guardrails require same-tenant owner scope, public projection redaction,
  fail-closed controls, focused tests, review, and 211 runtime evidence.
- The run-control readiness action must point at the explicit resume request,
  not the generic copy endpoint.

## Contract

Add:

```text
POST /api/ai/runs/{run_id}/resume
```

Response:

```json
{
  "run_id": "run_resume_new",
  "session_id": "ses_existing",
  "status": "queued",
  "queue_position": 1,
  "queue_insight": {}
}
```

Behavior:

- Requires `require_principal`.
- Uses `get_authorized_run(..., for_update=True)`, so only the source run owner
  in the same tenant can resume and concurrent source requests serialize.
- Applies the same active-run admission gate as normal run creation before
  copying or enqueueing resume work.
- Rejects active source runs with `409 active_run`.
- Rejects non-active sources without reusable checkpoint outputs with
  `409 no_checkpoint_outputs`.
- Rejects repeated resume requests with `409 resume_already_active` when the
  same owner already has a queued or running child run copied from the source.
- Creates a new queued run with `copied_from_run_id` set to the source run.
- Reuses existing copy-run skill release, context snapshot, queue payload, and
  seeded resume-step paths.
- Records `source = resume_run` in context, queue, and seeded step metadata.
- Writes `resume_requested` on the source run and `run_resume_created` on the
  new run before enqueueing.
- Writes `run.resume` audit evidence scoped to the source run.
- Does not start autonomous retry scheduling, subagent dispatch, high-risk tool
  execution, or new sandbox behavior.

## Implementation Shape

Keep route code in `app/routes/runs.py`:

- Update run-control readiness so the `resume` action points to
  `/api/ai/runs/{run_id}/resume`.
- Add `resume_run(...)` next to existing copy and retry controls.
- Reuse `prepare_copied_run_for_queue(...)` with `source="resume_run"`.
- Return the same `RunControlResponse` shape as copy and retry.

Keep repository code in `app/repositories.py`:

- Add `get_active_resume_for_source_run(...)` for same-owner queued/running
  resume idempotency.
- Add `resume_run_as_new_task(...)`.
- Lock the source run, reject active status, require completed checkpoint
  outputs, reject active same-source child work, then call the existing
  `copy_run_as_new_task(...)`.
- Record source/new-run events and audit after the new run row exists.

## Verification

Focused local tests:

- Readiness returns a resume action pointing to `/api/ai/runs/{run_id}/resume`.
- Resume creates a queued copied run, enqueues it, and records `resume_run`
  context and seeded step metadata.
- Resume rejects active sources before copying.
- Resume rejects sources without completed checkpoint output before copying.
- Resume rejects repeated same-source queued/running child runs with
  `resume_already_active`.
- Resume writes source/new-run events and `run.resume` audit.
- Existing copy and retry control tests keep passing.
- Source-authority docs tests keep passing.

211 smoke:

- Health is OK.
- OpenAPI contains `/api/ai/runs/{run_id}/resume`.
- Seeded failed source run with checkpoint output can be resumed by owner and
  returns a queued new run.
- Repeated resume while the first child run is queued/running returns
  `409 resume_already_active`.
- Active source run returns `409 active_run`.
- Source without checkpoint output returns `409 no_checkpoint_outputs`.
- Other user receives 404.
- New run has `copied_from_run_id`, resume events, audit log, context snapshot
  `source = resume_run`, queue payload context `source = resume_run`, and
  checkpoint reuse step seeding.
- Smoke rows are cleaned up.

## Self-Review

- No placeholder requirements remain.
- The endpoint is write-side but stays behind existing queue, skill, context,
  permission, and sandbox gates.
- The slice keeps executor private payloads out of user-facing projections.
- The slice is intentionally smaller than autonomous multi-agent scheduling.
