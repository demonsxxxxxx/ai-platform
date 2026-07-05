# B4 Skill Pinned Snapshot Design

Date: 2026-07-05

Status: approved source-slice design. This document does not claim review,
merge, 211 verification, or B4 gate closure.

## Goal

Move B4 Skill Productization forward with the smallest PR-ready source slice
for B4-G5: runs must carry a pinned Skill snapshot that records the exact
selected Skill package summary, release-lock summary, dependency evidence
reference, selected file summaries, and used-skill telemetry without exposing
raw release decisions, package bytes, storage keys, or executor-private data.

## Scope

This slice is source-level only. It does not add a database migration, a new
runtime deployment, a public release gate, or a full release-review workflow.
It uses existing `run_skill_snapshots.source_json` to store a safe governance
summary under `snapshot_governance`, and relies on the existing projection
redaction path before any admin run detail is returned.

In scope:

- Build a deterministic per-manifest `snapshot_governance` summary.
- Attach that summary to queue-time Skill manifests after final release version
  locking.
- Preserve that summary when the worker persists run Skill snapshots.
- Include selected file summaries as path, size, and SHA-256 only.
- Prove public/admin projections do not leak raw `release_decision`,
  `storage_key`, `content_base64`, or forbidden release-policy keys.
- Keep status labels conservative: this supports `local partial` or `PR ready`
  only, not `reviewed`, `merged`, `211 verified`, or `gate closable`.

Out of scope:

- Full B4 upload/import productization.
- Full immutable-version migration work.
- Release approval enforcement before promote.
- Runtime 211 smoke.
- Closing G6/B4.

## Architecture

`app.skills.pinning` remains the source of Skill manifest pinning behavior. It
will add a small governance-summary builder that consumes existing manifest
pins and a final release decision payload. The summary is attached to each
manifest as `snapshot_governance`; it contains only stable, non-secret fields.

`app.routes.runs` attaches the summary immediately after the final locked
version is chosen. This covers normal run creation and copied runs. `app.worker`
then persists the summary into `source_json.snapshot_governance`, using the
payload manifest as the authoritative fallback if an executor returns a manifest
without that field.

`app.repositories` does not need a schema change. Existing snapshot projection
already sanitizes `source_json`; tests will prove the new summary survives
sanitization while forbidden raw fields are removed.

## Safe Summary Contract

Schema version: `ai-platform.skill-pinned-snapshot-governance.v1`.

Required fields:

- `schema_version`
- `snapshot_source`: fixed to `platform_release_lock`
- `release_lock`: safe release-lock summary without raw policy keys.
- `manifest`: safe manifest summary with `source_kind` and
  `selected_file_count`.
- `selected_files`: list of file summaries with `relative_path`, `size_bytes`,
  and `sha256`.
- `dependency_evidence`: safe dependency review pointer with `status`, `ref`,
  and `dependency_count`.
- `does_not_close_b4_or_211`: `true`

Forbidden fields inside this contract:

- `release_decision`
- `storage_key`
- `content_base64`
- `skill_version`
- `content_hash`
- `policy_active`
- `channel`
- `selected_version`
- `selected_track`
- `rollout`
- `digest`
- `current_version`
- `previous_version`
- `fallback_version`

## Testing

Targeted tests must prove:

- Pinning builds deterministic file summaries and omits package bytes.
- Route-created queue payloads include `snapshot_governance` after final release
  locking.
- Worker persistence stores the summary for executor-returned manifests and
  ragflow fallback manifests.
- Repository/admin projection preserves the safe summary and strips forbidden
  nested raw fields.
- Existing release decision lock failure behavior remains fail-closed.

Required source verification before PR-ready status:

- `python -m compileall -q app tools scripts`
- `python -m pytest tests/test_skill_pinning.py tests/test_routes.py tests/test_worker.py tests/test_repositories.py -q --basetemp .pytest-tmp`
- Relevant readiness command if changed; otherwise document that no readiness
  command was changed.
- `git diff --check`

## Multi-Agent Use

The main session owns branch integration and code edits unless a sub-agent is
given a disjoint write set. A sub-agent review is required before claiming
`PR ready`. If delegation cannot prove model/reasoning specifics, record review
as inherited/default rather than a model-specific gate.
