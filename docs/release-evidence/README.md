# ai-platform Release Evidence Index

Date: 2026-06-12

This directory is the repository-owned location for reviewed, redacted release
evidence entries. It is an index and contract baseline only. This contract
does not close G9.

Generate the current readiness contract from the repository root:

```powershell
python tools/release_evidence_readiness.py --format markdown
python tools/release_evidence_readiness.py --format json
```

Generate the source-level export acceptance preflight separately:

```powershell
python tools/release_evidence_export_acceptance.py --format markdown
python tools/release_evidence_export_acceptance.py --format json
```

The preflight scans reviewed evidence entries and emits only a safe reviewed
index with entry path, IDs, commit, gate, issue/PR refs, artifact kind,
timestamps, redaction status, and review status. It does not include
`source_ref`, `evidence_ref`, executor-private payloads, raw storage keys,
sandbox work directories, or secret-like data. Older reviewed runtime smoke
entries that predate `runtime_subject_commit_sha` are excluded from the safe
index instead of blocking current export acceptance.

Generate the current Foundation Alpha POC operator summary separately:

```powershell
python tools/foundation_alpha_readiness.py --format markdown
python tools/foundation_alpha_readiness.py --format json
```

The Foundation Alpha operator summary is derived from reviewed evidence plus
the current source tree revision. Read `runtime_source_relation` before using
the summary as context. If it reports `source_synced_runtime_pending` and
`current_source_verified_by_running_runtime=false`, the recorded 211 smoke still
describes the verified runtime subject, while newer source commits still require
rollout and smoke evidence before they are runtime-verified.

The summary exposes that recorded evidence subject as
`verified_runtime_subject`. When its `evidence_scope` is
`reviewed_historical_runtime_evidence`, the image and commit identify the latest
reviewed, redacted 211 evidence record, not a live runtime claim for the current
source tree. Use
`controlled_poc_loop_verified_for_current_source=true` before treating the
controlled POC loop as verified for the exact current source revision. If
`current_source_exact_runtime_commit_match=false` but
`runtime_source_relation.status=runtime_current_for_runtime_relevant_source`,
then only `runtime_relevant_source_verified_by_running_runtime=true` may be
used: the runtime subject has verified all runtime-affecting source while later
docs/tests/evidence/readiness records remain outside the running image.
`current_source_verified_by_running_runtime` and
`controlled_poc_loop_verified_for_current_source` must remain false in that
state, and any non-empty `runtime_affecting_dirty_paths` must still block the
runtime-relevant claim.

On 211 source archives that are not Git worktrees, the summary may use a
local-only `.ai-platform-source-snapshot.json` marker to preserve that same
distinction after a docs/tests/evidence-only source sync. The marker must match
the local `.ai-platform-source-revision`, name the runtime subject commit, and
declare empty runtime-affecting changes; missing or invalid markers fail closed.

The readiness schema is `ai-platform.release-evidence-readiness.v1`. The entry
schema is `ai-platform.release-evidence-entry.v1`. The retention policy schema
is `ai-platform.release-evidence-retention-policy.v1`.

The G9 trace/audit export contract is generated separately:

```powershell
python tools/trace_audit_export_readiness.py --format markdown
python tools/trace_audit_export_readiness.py --format json
```

It uses schema `ai-platform.trace-audit-export-readiness.v1` and nested
contract schema `ai-platform.trace-audit-export-contract.v1`.

The release-evidence export acceptance preflight uses schema
`ai-platform.release-evidence-export-acceptance.v1`. It is embedded under
`ai-platform.release-evidence-readiness.v1` as `export_acceptance` and under
Admin Observability readiness as part of the release-evidence evidence block.
It does not close G9.

## Export Location

Release evidence entries belong under:

```text
docs/release-evidence/<gate>/<commit_sha>/<evidence_id>.json
```

The index for the evidence tree is:

```text
docs/release-evidence/README.md
```

Entries must be appended only after review and redaction. The evidence tree is
for public/admin operational proof, not executor private payloads, raw storage
keys, sandbox workdirs, secret material, bearer tokens, database URLs, or Redis
URLs.

For runtime smoke evidence, `commit_sha` is the verified subject commit that was
running or otherwise under review when the smoke was captured. The Git commit
that introduced or last updated an evidence file is intentionally not embedded
inside the JSON record, because a commit cannot contain its own final hash. Use
VCS history for the record commit. For `211_runtime_smoke` entries,
`runtime_subject_commit_sha`, `source_ref.runtime_source_marker`, and API/worker
image labels must all point to the same runtime subject commit.

## Reviewed Entries

| Date | Gate | Commit | Evidence | Status |
| --- | --- | --- | --- | --- |
| 2026-06-12 | Foundation Alpha POC | `2384e19dcac2e39fbcf9c27dc990f5774d391422` | [`2026-06-12-211-foundation-alpha-poc-2384e19-governance-runtime-smoke.json`](foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-governance-runtime-smoke.json) | Active 211 Admin Runtime governance smoke passed against `ai-platform:2384e19-context-source-provenance` using operator verifier source `8206690`; G6 remains partial and not production gate closure. |
| 2026-06-12 | Foundation Alpha POC | `2384e19dcac2e39fbcf9c27dc990f5774d391422` | [`2026-06-12-211-foundation-alpha-poc-2384e19-context-source-provenance-auth-rbac-smoke.json`](foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-context-source-provenance-auth-rbac-smoke.json) | Active 211 Auth/RBAC smoke passed against `ai-platform:2384e19-context-source-provenance`; not production gate closure. |
| 2026-06-12 | Foundation Alpha POC | `2384e19dcac2e39fbcf9c27dc990f5774d391422` | [`2026-06-12-211-foundation-alpha-poc-2384e19-context-source-provenance-smoke.json`](foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-context-source-provenance-smoke.json) | Active 211 runtime smoke passed for the controlled context snapshot source-provenance slice; context public projection returned `summary_source=chat_stream`; not G9 or production closure. |
| 2026-06-12 | Foundation Alpha POC | `e274d78b21c22fdf4f56a8cf8b31a0480d42c22f` | [`2026-06-12-211-foundation-alpha-poc-e274d78-auth-rbac-smoke.json`](foundation-alpha-poc/e274d78b21c22fdf4f56a8cf8b31a0480d42c22f/2026-06-12-211-foundation-alpha-poc-e274d78-auth-rbac-smoke.json) | Historical 211 Auth/RBAC smoke passed against `ai-platform:e274d78-g9-runtime-readiness-tools`; superseded by `2384e19`. |
| 2026-06-12 | Foundation Alpha POC | `e274d78b21c22fdf4f56a8cf8b31a0480d42c22f` | [`2026-06-12-211-foundation-alpha-poc-e274d78-runtime-readiness-tools-smoke.json`](foundation-alpha-poc/e274d78b21c22fdf4f56a8cf8b31a0480d42c22f/2026-06-12-211-foundation-alpha-poc-e274d78-runtime-readiness-tools-smoke.json) | Historical 211 runtime smoke passed for the controlled Foundation Alpha POC loop and container-side release-evidence export acceptance preflight against `ai-platform:e274d78-g9-runtime-readiness-tools`; superseded by `2384e19`. |
| 2026-06-12 | Foundation Alpha POC | `d95107da2b5691781518bdbb8c4e5e76409869f3` | [`2026-06-12-211-foundation-alpha-poc-d95107d-auth-rbac-smoke.json`](foundation-alpha-poc/d95107da2b5691781518bdbb8c4e5e76409869f3/2026-06-12-211-foundation-alpha-poc-d95107d-auth-rbac-smoke.json) | Historical merged-main PR #30 Auth/RBAC smoke passed on 211 against `ai-platform:d95107d-context-projection`; superseded by `e274d78`. |
| 2026-06-12 | Foundation Alpha POC | `d95107da2b5691781518bdbb8c4e5e76409869f3` | [`2026-06-12-211-foundation-alpha-poc-d95107d-context-projection-smoke.json`](foundation-alpha-poc/d95107da2b5691781518bdbb8c4e5e76409869f3/2026-06-12-211-foundation-alpha-poc-d95107d-context-projection-smoke.json) | Historical merged-main PR #30 211 runtime smoke passed for context public projection and the controlled Foundation Alpha POC loop against `ai-platform:d95107d-context-projection`; superseded by `e274d78`. |
| 2026-06-12 | Foundation Alpha POC | `a63dbbd0b474cce3702b3485e6589f86155cf5aa` | [`2026-06-12-211-foundation-alpha-poc-a63dbbd-auth-rbac-smoke.json`](foundation-alpha-poc/a63dbbd0b474cce3702b3485e6589f86155cf5aa/2026-06-12-211-foundation-alpha-poc-a63dbbd-auth-rbac-smoke.json) | Historical PR #30 Auth/RBAC smoke passed on 211 against `ai-platform:a63dbbd-context-summary-source`; superseded by `e274d78`. |
| 2026-06-12 | Foundation Alpha POC | `a63dbbd0b474cce3702b3485e6589f86155cf5aa` | [`2026-06-12-211-foundation-alpha-poc-a63dbbd-smoke.json`](foundation-alpha-poc/a63dbbd0b474cce3702b3485e6589f86155cf5aa/2026-06-12-211-foundation-alpha-poc-a63dbbd-smoke.json) | Historical PR #30 211 runtime smoke passed for the controlled Foundation Alpha POC loop against `ai-platform:a63dbbd-context-summary-source`; superseded by `e274d78`. |
| 2026-06-12 | Foundation Alpha POC | `458f6056dd0fa533162e780a303d79ce1b3d0eec` | [`2026-06-12-211-foundation-alpha-poc-458f605-auth-rbac-smoke.json`](foundation-alpha-poc/458f6056dd0fa533162e780a303d79ce1b3d0eec/2026-06-12-211-foundation-alpha-poc-458f605-auth-rbac-smoke.json) | Historical Auth/RBAC smoke passed on 211 against `ai-platform:458f605-auth-rbac-redaction`; superseded by `a63dbbd`. |
| 2026-06-12 | Foundation Alpha POC | `458f6056dd0fa533162e780a303d79ce1b3d0eec` | [`2026-06-12-211-foundation-alpha-poc-458f605-smoke.json`](foundation-alpha-poc/458f6056dd0fa533162e780a303d79ce1b3d0eec/2026-06-12-211-foundation-alpha-poc-458f605-smoke.json) | Historical 211 runtime smoke passed for the controlled Foundation Alpha POC loop against `ai-platform:458f605-auth-rbac-redaction`; superseded by `a63dbbd`. |
| 2026-06-11 | Foundation Alpha POC | `9b02836262fb0f238a7f90b9705bf39a8b298158` | [`2026-06-11-211-foundation-alpha-poc-9b02836-auth-rbac-smoke.json`](foundation-alpha-poc/9b02836262fb0f238a7f90b9705bf39a8b298158/2026-06-11-211-foundation-alpha-poc-9b02836-auth-rbac-smoke.json) | Historical Auth/RBAC smoke passed on 211 against `ai-platform:9b02836-context-output`; superseded by `458f605`. |
| 2026-06-11 | Foundation Alpha POC | `9b02836262fb0f238a7f90b9705bf39a8b298158` | [`2026-06-11-211-foundation-alpha-poc-9b02836-context-output-smoke.json`](foundation-alpha-poc/9b02836262fb0f238a7f90b9705bf39a8b298158/2026-06-11-211-foundation-alpha-poc-9b02836-context-output-smoke.json) | Historical 211 runtime smoke passed for the controlled Foundation Alpha POC loop; superseded by `458f605`. |
| 2026-06-11 | Foundation Alpha POC | `8f454696be0e9c532fa86bc61ef353e4d3dec4f8` | [`2026-06-11-211-foundation-alpha-poc-8f45469-auth-rbac-smoke.json`](foundation-alpha-poc/8f454696be0e9c532fa86bc61ef353e4d3dec4f8/2026-06-11-211-foundation-alpha-poc-8f45469-auth-rbac-smoke.json) | Historical Auth/RBAC smoke passed on 211 against `ai-platform:8f45469-foundation-alpha-readiness`; superseded by `9b02836`. |
| 2026-06-11 | Foundation Alpha POC | `8f454696be0e9c532fa86bc61ef353e4d3dec4f8` | [`2026-06-11-211-foundation-alpha-poc-8f45469-smoke.json`](foundation-alpha-poc/8f454696be0e9c532fa86bc61ef353e4d3dec4f8/2026-06-11-211-foundation-alpha-poc-8f45469-smoke.json) | Historical 211 runtime smoke passed for the controlled Foundation Alpha POC loop; superseded by `9b02836`. |
| 2026-06-11 | Foundation Alpha POC | `faa7ad6aa61637cbcdf3a22ce81de119762e96bf` | [`2026-06-11-211-foundation-alpha-poc-faa7ad6-auth-rbac-smoke.json`](foundation-alpha-poc/faa7ad6aa61637cbcdf3a22ce81de119762e96bf/2026-06-11-211-foundation-alpha-poc-faa7ad6-auth-rbac-smoke.json) | Historical Auth/RBAC smoke passed on 211 against `ai-platform:faa7ad6-foundation-alpha-poc`; superseded by `8f45469`. |
| 2026-06-11 | Foundation Alpha POC | `faa7ad6aa61637cbcdf3a22ce81de119762e96bf` | [`2026-06-11-211-foundation-alpha-poc-faa7ad6-smoke.json`](foundation-alpha-poc/faa7ad6aa61637cbcdf3a22ce81de119762e96bf/2026-06-11-211-foundation-alpha-poc-faa7ad6-smoke.json) | Historical 211 runtime smoke passed for the controlled Foundation Alpha POC loop; superseded by `8f45469`. |
| 2026-06-11 | Foundation Alpha POC | `a3f1d739e12686cba2e0b309de26a4e1127bd3a5` | [`2026-06-11-211-foundation-alpha-poc-a3f1d73-auth-rbac-smoke.json`](foundation-alpha-poc/a3f1d739e12686cba2e0b309de26a4e1127bd3a5/2026-06-11-211-foundation-alpha-poc-a3f1d73-auth-rbac-smoke.json) | Historical Auth/RBAC smoke passed on 211 against `ai-platform:a3f1d73-foundation-alpha-poc`; superseded by `8f45469`. |
| 2026-06-11 | Foundation Alpha POC | `a3f1d739e12686cba2e0b309de26a4e1127bd3a5` | [`2026-06-11-211-foundation-alpha-poc-a3f1d73-smoke.json`](foundation-alpha-poc/a3f1d739e12686cba2e0b309de26a4e1127bd3a5/2026-06-11-211-foundation-alpha-poc-a3f1d73-smoke.json) | Historical 211 runtime smoke passed for the controlled Foundation Alpha POC loop; superseded by `8f45469`. |
| 2026-06-11 | Foundation Alpha POC | `8c0cffca63bc747fad0a5771f209acc8a608ab9e` | [`2026-06-11-211-foundation-alpha-poc-current-main-auth-rbac-smoke.json`](foundation-alpha-poc/8c0cffca63bc747fad0a5771f209acc8a608ab9e/2026-06-11-211-foundation-alpha-poc-current-main-auth-rbac-smoke.json) | Historical current-main Auth/RBAC smoke passed on 211 against `ai-platform:8c0cffc-foundation-alpha-poc`; superseded by `a3f1d73`. |
| 2026-06-11 | Foundation Alpha POC | `8c0cffca63bc747fad0a5771f209acc8a608ab9e` | [`2026-06-11-211-foundation-alpha-poc-current-main-smoke.json`](foundation-alpha-poc/8c0cffca63bc747fad0a5771f209acc8a608ab9e/2026-06-11-211-foundation-alpha-poc-current-main-smoke.json) | Historical 211 runtime smoke passed for the controlled Foundation Alpha POC loop; superseded by `a3f1d73`. |
| 2026-06-11 | Foundation Alpha POC | `bf20432f9889efa8b367afdf512c641068ba30bc` | [`2026-06-11-211-foundation-alpha-poc-auth-rbac-smoke.json`](foundation-alpha-poc/bf20432f9889efa8b367afdf512c641068ba30bc/2026-06-11-211-foundation-alpha-poc-auth-rbac-smoke.json) | Auth/RBAC smoke passed against the existing merged runtime using verifier source `3a3da257484d4d430a7a26e00a6f1cdae39a2b12`; not production gate closure. |
| 2026-06-11 | Foundation Alpha POC | `bf20432f9889efa8b367afdf512c641068ba30bc` | [`2026-06-11-211-foundation-alpha-poc-merged-smoke.json`](foundation-alpha-poc/bf20432f9889efa8b367afdf512c641068ba30bc/2026-06-11-211-foundation-alpha-poc-merged-smoke.json) | Merged main 211 runtime smoke passed for the controlled POC loop; not production gate closure. |
| 2026-06-11 | Foundation Alpha POC | `3874281276c84a418bd08bda56d7ea55b52970b7` | [`2026-06-11-211-foundation-alpha-poc-smoke.json`](foundation-alpha-poc/3874281276c84a418bd08bda56d7ea55b52970b7/2026-06-11-211-foundation-alpha-poc-smoke.json) | 211 runtime smoke passed for the controlled POC loop; not production gate closure. |

## Entry Contract

Each `ai-platform.release-evidence-entry.v1` entry must include:

- `evidence_id`
- `commit_sha`
- `gate`
- `issue_refs`
- `artifact_kind`
- `captured_at`
- `source_ref`
- `evidence_ref`
- `redaction_scan_status`
- `review_status`

Conditional fields:

- `211_runtime_smoke`: `runtime_subject_commit_sha`

Accepted artifact kinds currently include:

- `211_runtime_smoke`
- `capacity_gate_readiness`
- `frontend_packaged_runtime_smoke`
- `frontend_release_traceability`
- `governance_readiness`
- `observability_readiness`

## Trace / Audit Export Contract

Future reviewed trace/audit export evidence belongs to the platform audit
domain path:

```text
audit.trace_exports.<export_id>
```

The `trace_audit_export_contract` allows only public/admin projection sources:

- `run_event_public_projection`
- `audit_event_public_projection`
- `admin_runtime_observability_summary`
- `release_evidence_entry`

This is a contract-only baseline. It does not export runtime data and does not
close G9. Remaining blockers are:

- `trace_audit_export_runtime_acceptance`
- `trace_audit_export_dashboard_acceptance`
- `trace_audit_export_211_acceptance`

## Retention Policy Contract

Release evidence currently has a source-level retention policy contract only.
It is not runtime-enforced and does not close G9.

- schema: `ai-platform.release-evidence-retention-policy.v1`
- status: `contract_only_not_runtime_enforced`
- default retention: 180 days
- minimum retention: 30 days
- delete requires review and applies only to reviewed, redacted evidence
  entries

The policy forbids deleting raw runtime payloads, executor private payloads,
raw storage keys, sandbox work directories, secret material, or unreviewed
evidence drafts through this reviewed release-evidence path.

## Open Gaps

- `release_evidence_runtime_export_acceptance`
- `release_evidence_retention_runtime_acceptance`

This contract creates the source-level export location and entry shape. It does
not close the gate until runtime export acceptance, runtime retention
acceptance, review workflow, and deployment evidence are all proven. Passing the
source-level export acceptance preflight is only a precondition for operator
review; it is not runtime export evidence.
