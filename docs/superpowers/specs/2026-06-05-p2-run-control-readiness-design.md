# P2 Run Control Readiness Snapshot Design

## Purpose

Add a read-only run control readiness contract for P2 Long Task / Multi-Agent
Runtime foundations. The contract tells ordinary users and admins whether the
current run can be cancelled, resumed through an explicit resume request backed
by copy-run checkpoint reuse, or retried later, and why each action is enabled
or blocked.

This slice does not add retry scheduling, autonomous multi-agent dispatch,
new sandbox/tool execution, or a new frontend entry. It exposes current platform
state as a public projection so the next P2 runtime work has an auditable
contract before behavior expands.

## Sources

- Current PRD: `docs/superpowers/specs/2026-05-29-ai-platform-final-product-prd.md`
- Foundation roadmap: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Guardrails: `docs/agent-rules/ai-platform-guardrails.md`
- Existing code: `app/routes/runs.py`, `app/repositories.py`
- Existing tests: `tests/test_run_control_routes.py`,
  `tests/test_event_playback_routes.py`, `tests/test_source_authority_docs.py`

## Contract

Route: `GET /api/ai/runs/{run_id}/control/readiness`

Contract version: `ai-platform.run-control-readiness.v1`

The route is owner-scoped through the existing `get_authorized_run` repository
function. It returns a sanitized run summary, queue insight when useful, and
three action cards:

- `cancel`: enabled for `queued` or `running` runs; blocked for terminal runs or
  already requested cancellation.
- `resume`: enabled when a run has at least one succeeded step with reusable
  output; blocked when no checkpoint output exists or the run is still active.
- `retry`: read-only preview only in this slice. It is blocked with
  `retry_runtime_not_enabled` unless the run is failed or dead-lettered and the
  future retry policy gate is implemented.

The response includes checkpoint candidates derived from existing run steps.
For ordinary users, candidates must not expose raw skill ids, MCP tool ids,
resource limits, sandbox mode, executor private payloads, runtime paths, or raw
step output. Admin users may keep operational step controls already allowed by
`run_step_response`, but still must not receive secret-like payloads.

## Projection Shape

```json
{
  "contract_version": "ai-platform.run-control-readiness.v1",
  "run": {
    "run_id": "run-a",
    "status": "failed",
    "skill_id": null,
    "capability_id": "document_review"
  },
  "actions": {
    "cancel": {
      "enabled": false,
      "reason": "terminal_run",
      "method": "POST",
      "href": "/api/ai/runs/run-a/cancel"
    },
    "resume": {
      "enabled": true,
      "reason": "checkpoint_outputs_available",
      "method": "POST",
      "href": "/api/ai/runs/run-a/resume"
    },
    "retry": {
      "enabled": false,
      "reason": "retry_runtime_not_enabled",
      "method": null,
      "href": null
    }
  },
  "checkpoint_candidates": [
    {
      "step_id": "step-a",
      "step_key": "review",
      "status": "succeeded",
      "role": "reviewer",
      "reusable": true,
      "reason": "output_available"
    }
  ],
  "queue_insight": null
}
```

## Error Handling

- Unauthorized or missing runs return `404 {"detail": "run_not_found"}`.
- Malformed step payloads are treated as non-reusable instead of throwing.
- Ordinary-user output uses existing public projection helpers and additional
  readiness-specific redaction for reusable checkpoint candidates.

## Testing

Add focused tests in `tests/test_run_control_routes.py`:

- failed run with succeeded step output returns resume enabled and cancel
  blocked, with the resume action pointing at `/api/ai/runs/{run_id}/resume`.
- queued run returns cancel enabled and queue insight.
- ordinary-user readiness response does not expose raw skill ids, resource
  limits, sandbox mode, runtime paths, private payloads, or raw output.
- missing run returns 404 and does not list steps.

Run local verification:

- `python -m pytest tests/test_run_control_routes.py -q --basetemp .pytest-tmp\\p2-run-control-readiness-routes`
- `python -m pytest tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\\p2-run-control-readiness-docs`
- `python -m compileall -q app tools scripts`
- `python -m pytest -q --basetemp .pytest-tmp\\p2-run-control-readiness-full`

## Deployment Smoke

After review and local full-suite pass, deploy to 211 and verify:

- API and worker image labels point to the implemented code commit.
- `/api/ai/health` returns 200.
- OpenAPI includes `/api/ai/runs/{run_id}/control/readiness`.
- A seeded or existing same-tenant run returns the readiness contract.
- Ordinary-user response contains no forbidden runtime/private markers.

## Non-Goals

- No retry scheduler or dead-letter requeue implementation.
- No new worker resume behavior beyond the existing copy-run checkpoint reuse
  execution path.
- No new frontend route.
- No change to sandbox provider, tool permission policy, or executor behavior.
