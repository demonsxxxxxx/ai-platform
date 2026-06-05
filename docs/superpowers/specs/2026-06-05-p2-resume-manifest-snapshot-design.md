# P2 Resume Manifest Snapshot Design

## Goal

Add a read-only P2 run resume manifest contract so an authorized user can
inspect how a copied run intends to reuse checkpoint-complete source steps
before any future resume scheduler or retry runtime is enabled.

This slice supports the G10 Long Task / Multi-Agent path by making resume intent
auditable. It does not start retry scheduling, autonomous subagent dispatch,
new sandbox behavior, or write-capable tool execution.

## Contract

Route: `GET /api/ai/runs/{run_id}/resume/manifest`

Contract version: `ai-platform.run-resume-manifest.v1`

Authorization: use the existing `get_authorized_run` owner/same-tenant check.
Missing or unauthorized runs return `404 {"detail": "run_not_found"}` and must
not load run steps.

The response shape is:

```json
{
  "contract_version": "ai-platform.run-resume-manifest.v1",
  "run": {
    "run_id": "run-new",
    "status": "queued",
    "agent_id": "general-agent",
    "skill_id": null,
    "capability_id": "general_chat"
  },
  "source_run_id": "run-old",
  "resume_enabled": true,
  "reason": "reuse_pending",
  "counts": {
    "total": 2,
    "reuse_pending": 1,
    "rerun": 1,
    "pending": 2,
    "running": 0,
    "succeeded": 0,
    "failed": 0,
    "cancelled": 0
  },
  "steps": [
    {
      "step_id": "step-code",
      "step_key": "code",
      "status": "pending",
      "title": "Code",
      "role": "coding",
      "sequence": 1,
      "depends_on": [],
      "reuse_intent": "reuse_pending",
      "source_run_id": "run-old"
    }
  ]
}
```

## Resume Semantics

The manifest is derived from existing `run_steps.payload_json` values seeded by
copy-run, but it treats that payload as untrusted for public projection:

- `checkpoint_reuse_pending: true` means the copied run expects to reuse a
  source step output.
- `copied_from_run_id` links a reused copied step to its source run only after
  safe run id validation and an additional `get_authorized_run` check for the
  current principal. Unsafe, unauthorized, path-like, storage-key-like, or
  hash-like values are dropped.
- `depends_on`, `step_key`, `title`, and `role` remain visible only as
  public-safe scalars. Runtime paths, storage keys, command fingerprints,
  secret-like strings, and raw skill/agent ids for ordinary users are dropped
  or replaced with safe step ids.

If no step has pending checkpoint reuse, the route still returns a manifest
with `source_run_id: null`, `resume_enabled: false`, and
`reason: "no_reuse_pending"`. This lets frontend code render a stable empty
state for normal runs without needing a second route.

## Redaction

Ordinary users must not see raw skill ids, internal agent ids, raw checkpoint
outputs, resource limits, sandbox modes, work directories, private payloads,
storage keys, command fingerprints, secret-like strings, or executor private
payloads.

The implementation will reuse the existing public run summary and readiness
public text helpers, plus manifest-specific rejection for hash-like command
fingerprints. Admins keep the same raw-skill visibility already allowed by
existing run projection contracts, but the manifest still does not expose
checkpoint output content, unsafe or unauthorized source run ids, runtime
paths, storage keys, or executor private payload.

## Testing

Focused tests cover:

- A copied run with one reuse-pending step and one rerun step returns the
  expected manifest, counts, source run id, and sanitized step linkage.
- An ordinary-user manifest redacts raw skill ids, runtime paths, sandbox
  settings, resource limits, raw outputs, and private payload fields from public
  scalars and payloads.
- A normal run without copied resume metadata returns a stable disabled
  manifest instead of pretending resume is available.
- A missing or unauthorized run returns `404` without loading steps.

Local verification:

- `python -m pytest tests/test_run_control_routes.py -q --basetemp .pytest-tmp\p2-resume-manifest-routes`
- `python -m compileall -q app tools scripts`
- `python -m pytest tests/test_run_control_routes.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-resume-manifest-focused`
- `python -m pytest -q --basetemp .pytest-tmp\p2-resume-manifest-full`

211 verification after deploy:

- `/api/ai/health` returns `200`.
- `/openapi.json` exposes `/api/ai/runs/{run_id}/resume/manifest`.
- A seeded same-tenant ordinary-user copied run returns
  `ai-platform.run-resume-manifest.v1`.
- The seeded response contains no raw skill ids, runtime paths, sandbox modes,
  resource limits, raw checkpoint outputs, storage keys, or private payloads.
- Smoke data cleanup is verified with zero remaining seeded rows.
