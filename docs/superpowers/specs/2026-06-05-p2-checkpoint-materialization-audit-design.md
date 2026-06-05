# P2 Checkpoint Materialization Audit Snapshot Design

## Goal

Add a read-only P2 checkpoint audit contract so an authorized user can inspect
which checkpoints are reusable, which artifacts are linked to them, and which
checkpoint/materialization gaps remain before retry, resume scheduler, or
autonomous multi-agent runtime is enabled.

This slice supports the G10 Long Task / Multi-Agent path by making checkpoint
materialization state auditable. It does not start retry scheduling, autonomous
subagent dispatch, high-risk tool execution, or new sandbox behavior.

## Contract

Route: `GET /api/ai/runs/{run_id}/checkpoints/audit`

Contract version: `ai-platform.run-checkpoint-audit.v1`

The route is owner-scoped through the existing `get_authorized_run` check. It
loads only the authorized run's current `run_steps` and `artifacts`.

Example shape:

```json
{
  "contract_version": "ai-platform.run-checkpoint-audit.v1",
  "run": {
    "run_id": "run-a",
    "status": "failed",
    "skill_id": null
  },
  "counts": {
    "checkpoints": 1,
    "resume_reusable": 1,
    "artifact_materialized": 1,
    "step_only": 0,
    "artifact_only": 0,
    "incomplete": 0,
    "gaps": 0,
    "uncheckpointed_reusable_steps": 0
  },
  "checkpoints": [
    {
      "checkpoint_id": "checkpoint-a",
      "audit_state": "materialized",
      "resume_reusable": true,
      "artifact_materialized": true,
      "step_ids": ["step-code"],
      "artifact_ids": ["artifact-report"],
      "reuse": {
        "pending": 0,
        "reused": 1
      },
      "gaps": []
    }
  ],
  "uncheckpointed_reusable_steps": []
}
```

## State Rules

- `resume_reusable` is true only when at least one succeeded step for the
  checkpoint has `payload_json.output` present.
- `artifact_materialized` is true only when at least one authorized artifact
  has safe checkpoint lineage for that checkpoint and does not point to an
  existing producer step from a different checkpoint. If an artifact has a
  safe producer step id that is no longer present in the run projection, the
  checkpoint remains artifact-only with a `producer_step_missing` gap only
  when there is no current step evidence for that checkpoint. If the checkpoint
  already has current step evidence, missing producer linkage is a gap and does
  not satisfy `artifact_materialized`.
- `audit_state` is:
  - `materialized` when both `resume_reusable` and `artifact_materialized` are
    true.
  - `step_only` when a checkpoint has step evidence but no artifact lineage.
  - `artifact_only` when a checkpoint has artifact lineage but no matching step.
  - `incomplete` when checkpoint evidence exists but no reusable output or
    artifact materialization is present.
- A succeeded step with output but no safe checkpoint id is returned in
  `uncheckpointed_reusable_steps` with `reason: missing_checkpoint_id`.
- A checkpoint id that contains an ordinary user's raw skill or internal agent
  id is treated as unsafe for that ordinary-user projection.
- A checkpoint with one reusable step and other pending or failed steps remains
  reusable; it must not add `no_reusable_output` unless no step in that
  checkpoint has reusable output.
- An artifact whose `source_step_id` points to an existing step from a different
  checkpoint adds `producer_checkpoint_mismatch` and does not satisfy
  `artifact_materialized` for the artifact's checkpoint id.
- An artifact with no `source_step_id` adds `artifact_source_step_missing` and
  does not satisfy `artifact_materialized`.
- An artifact with an unsafe `source_step_id` adds `artifact_source_step_unsafe`
  and does not satisfy `artifact_materialized`.
- Unsafe checkpoint ids, source ids, storage keys, runtime paths, command
  fingerprints, raw skill ids, resource limits, sandbox settings, and executor
  private payloads are not returned to ordinary users.

## Implementation

The implementation stays in `app/routes/runs.py` to reuse the existing run
authorization, public run summary, artifact card, and safe graph id helpers.
The projection should use raw step rows only for boolean audit facts such as
`output_available`, `checkpoint_reuse_pending`, and `checkpoint_reused`; it
must not copy raw output content into the response.

The route will:

1. Authorize the run through `repositories.get_authorized_run`.
2. Load `repositories.list_run_steps` and `repositories.list_run_artifacts`.
3. Build deterministic checkpoint entries from safe step checkpoint ids and
   safe artifact lineage checkpoint ids.
4. Return counts, checkpoint entries, and uncheckpointed reusable step gaps.

No repository method, schema, queue, worker, sandbox, or retry behavior changes
are required.

## Tests

Focused tests should prove:

- An ordinary-user audit response links step evidence and artifact lineage
  without exposing raw skill ids, raw output, runtime paths, storage keys,
  resource limits, sandbox settings, command fingerprints, or private payloads.
- An artifact-only checkpoint returns `audit_state: artifact_only` and a
  `producer_step_missing` gap.
- An artifact with missing or unsafe `source_step_id` does not satisfy
  `artifact_materialized` and reports the corresponding source-step gap.
- A succeeded output step with no safe checkpoint id appears as
  `uncheckpointed_reusable_steps`.
- A missing run returns 404 without loading steps or artifacts.

Verification commands:

```powershell
python -m pytest tests/test_run_control_routes.py -q --basetemp .pytest-tmp\p2-checkpoint-audit-routes
python -m pytest tests/test_run_control_routes.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-checkpoint-audit-focused
python -m compileall -q app tools scripts
python -m pytest -q --basetemp .pytest-tmp\p2-checkpoint-audit-full
```

211 smoke after deployment must verify:

- `/api/ai/health` returns 200.
- `/openapi.json` exposes `/api/ai/runs/{run_id}/checkpoints/audit`.
- A seeded same-tenant run returns `ai-platform.run-checkpoint-audit.v1`.
- Redaction blocks raw skill ids, raw output content, resource limits, sandbox
  fields, runtime paths, storage keys, command fingerprints, and private
  payloads.
- Smoke data is cleaned up.

## Non-Goals

- No retry scheduler, dead-letter requeue, or resume executor behavior.
- No autonomous subagent dispatch.
- No new sandbox lease/provider behavior.
- No Admin-only global checkpoint inventory.
- No artifact storage or manifest schema migration.
