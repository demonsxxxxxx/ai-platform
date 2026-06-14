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
For normal clean GitHub checkouts where the active runtime subject commit is
not available locally because it came from a squashed PR branch, the committed
`foundation-alpha-poc/source-runtime-relation-manifest.json` provides the same
runtime-affecting delta contract. The readiness tool may use it only when it
matches the current source tree or when newer source changes after the manifest
are runtime-neutral.

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
`runtime_subject_commit_sha`, `source_ref.runtime_source_marker`, and the source
revision / OCI revision image labels must point to the same runtime subject
commit. If legacy labels such as `ai-platform.runtime-subject` or compose
environment-file labels still reference an older rollout, the reviewed evidence
entry must record that as a stale-label follow-up instead of using the entry as
G0 source-authority closure.

## Reviewed Entries

| Date | Gate | Commit | Evidence | Status |
| --- | --- | --- | --- | --- |
| 2026-06-14 | Foundation Runtime Concurrency | `384474cf4f8b19f2f6bf558c43a62a860c76a367` | [`2026-06-14-211-foundation-alpha-poc-384474c-foundation-runtime-concurrency.json`](foundation-alpha-poc/384474cf4f8b19f2f6bf558c43a62a860c76a367/2026-06-14-211-foundation-alpha-poc-384474c-foundation-runtime-concurrency.json), [`2026-06-14-211-foundation-alpha-poc-384474c-foundation-runtime-concurrency-readiness.json`](foundation-alpha-poc/384474cf4f8b19f2f6bf558c43a62a860c76a367/2026-06-14-211-foundation-alpha-poc-384474c-foundation-runtime-concurrency-readiness.json) | Current 211 POC correctness evidence verifies 2 tenants, 4 users, 12 concurrent run creation/execution/cancel/retry cases, public context snapshots, context-pack version samples, fixture-agent trusted-header routing, and fail-closed fixture login behavior; supports Foundation Runtime concurrency evidence only and does not raise production concurrency defaults, open ordinary-user multi-agent, claim Docker sandbox hardening, enable long-term memory, or close department rollout. |
| 2026-06-14 | Foundation Runtime Concurrency | `3843395b180324b165cbca7c59b6d7e1a934e290-frc-context-pack-20260614-0535` | [`2026-06-14-211-foundation-alpha-poc-3843395-foundation-runtime-concurrency.json`](foundation-alpha-poc/3843395b180324b165cbca7c59b6d7e1a934e290/2026-06-14-211-foundation-alpha-poc-3843395-foundation-runtime-concurrency.json) | Superseded historical 211 POC correctness evidence: it verifies 2 tenants, 4 users, 12 concurrent run creation/execution/cancel/retry cases, public context snapshots, and context-pack version samples, but fixture-agent trusted-header routing and fail-closed fixture login behavior have been refreshed by `384474c`. Retained as history only, not accepted current Foundation Runtime concurrency closure evidence. |
| 2026-06-14 | Foundation Runtime Concurrency | `3843395b180324b165cbca7c59b6d7e1a934e290-fr-concurrency-local-20260614-0035` | [`foundation-runtime-concurrency-evidence-211-20260614-013347.json`](foundation-runtime-concurrency/3843395b180324b165cbca7c59b6d7e1a934e290-fr-concurrency-local-20260614-0035/foundation-runtime-concurrency-evidence-211-20260614-013347.json), [`foundation-runtime-concurrency-readiness-211-20260614-013347.json`](foundation-runtime-concurrency/3843395b180324b165cbca7c59b6d7e1a934e290-fr-concurrency-local-20260614-0035/foundation-runtime-concurrency-readiness-211-20260614-013347.json) | Superseded historical 211 evidence: it still records 2 tenants, 4 users, and 12 concurrent run creation/execution/cancel/retry cases, but current validation blocks it because it lacks public context projection and context-pack version samples. Retained as history only, not accepted current Foundation Runtime concurrency closure evidence. |
| 2026-06-13 | Foundation Alpha POC | `ac9a86bbea14a28748867cade8d80b2f9ff420ec` | [`2026-06-13-211-foundation-alpha-poc-ac9a86b-alert-trace-export-runtime-acceptance.json`](foundation-alpha-poc/ac9a86bbea14a28748867cade8d80b2f9ff420ec/2026-06-13-211-foundation-alpha-poc-ac9a86b-alert-trace-export-runtime-acceptance.json) | Current 211 alert/trace export runtime acceptance passed against `ai-platform:ac9a86b-s1-merged`; supports removing the alert/trace 211 acceptance blocker for this runtime subject but does not enable alert delivery, close G9, close signed Skill package/SBOM, close packaged frontend image release acceptance, or close production/G0 deployment-layout follow-ups. |
| 2026-06-13 | Foundation Alpha POC | `ac9a86bbea14a28748867cade8d80b2f9ff420ec` | [`2026-06-13-211-foundation-alpha-poc-ac9a86b-auth-rbac-smoke.json`](foundation-alpha-poc/ac9a86bbea14a28748867cade8d80b2f9ff420ec/2026-06-13-211-foundation-alpha-poc-ac9a86b-auth-rbac-smoke.json) | Current 211 Auth/RBAC smoke passed against `ai-platform:ac9a86b-s1-merged`; supports the ac9a86b Foundation Alpha POC security baseline but does not close production auth rollout, packaged frontend image acceptance, or production/G0 deployment-layout follow-ups. |
| 2026-06-13 | Foundation Alpha POC | `ac9a86bbea14a28748867cade8d80b2f9ff420ec` | [`2026-06-13-211-foundation-alpha-poc-ac9a86b-runtime-poc-smoke.json`](foundation-alpha-poc/ac9a86bbea14a28748867cade8d80b2f9ff420ec/2026-06-13-211-foundation-alpha-poc-ac9a86b-runtime-poc-smoke.json) | Current 211 runtime POC smoke passed against `ai-platform:ac9a86b-s1-merged`; verifies the controlled POC loop, context public projection, company-login audit evidence, and cross-user/cross-tenant artifact download and preview denial for this runtime subject, but does not close packaged frontend image, signed Skill package/SBOM, production, or independent review gates. |
| 2026-06-13 | Foundation Alpha POC | `ac9a86bbea14a28748867cade8d80b2f9ff420ec` | [`2026-06-13-211-foundation-alpha-poc-ac9a86b-governance-runtime-smoke.json`](foundation-alpha-poc/ac9a86bbea14a28748867cade8d80b2f9ff420ec/2026-06-13-211-foundation-alpha-poc-ac9a86b-governance-runtime-smoke.json) | Current 211 Admin Runtime governance smoke passed against `ai-platform:ac9a86b-s1-merged`; supports the S1 governance projection and required governed skill, MCP permission, and memory/context readiness fields, but does not close signed Skill package/SBOM, dependency/license, visual dashboard, production governance rollout, ordinary-user multi-agent exposure, or packaged frontend image release acceptance gates. |
| 2026-06-13 | Foundation Alpha POC | `ac9a86bbea14a28748867cade8d80b2f9ff420ec` | [`2026-06-13-211-foundation-alpha-poc-ac9a86b-release-evidence-runtime-acceptance.json`](foundation-alpha-poc/ac9a86bbea14a28748867cade8d80b2f9ff420ec/2026-06-13-211-foundation-alpha-poc-ac9a86b-release-evidence-runtime-acceptance.json) | Current 211 release-evidence runtime acceptance passed against `ai-platform:ac9a86b-s1-merged`; supports removing the runtime export/retention blocker for this runtime subject but does not close signed Skill package/SBOM, packaged frontend image, production, or independent review gates. |
| 2026-06-13 | Foundation Alpha POC | `cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55` | [`2026-06-13-211-foundation-alpha-poc-cbbfaff-alert-trace-export-runtime-acceptance.json`](foundation-alpha-poc/cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55/2026-06-13-211-foundation-alpha-poc-cbbfaff-alert-trace-export-runtime-acceptance.json) | Historical 211 alert/trace export runtime acceptance passed against `ai-platform:cbbfaff-runtime-evidence-root-redaction-v2`; immediately superseded by `ac9a86b` and retained only as reviewed history. |
| 2026-06-13 | Foundation Alpha POC | `cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55` | [`2026-06-13-211-foundation-alpha-poc-cbbfaff-frontend-packaged-runtime-smoke-blocked.json`](foundation-alpha-poc/cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55/2026-06-13-211-foundation-alpha-poc-cbbfaff-frontend-packaged-runtime-smoke-blocked.json) | Historical 211 packaged frontend runtime smoke attempt remained `blocked_environment`; retained as blocker history only and still does not close `packaged_frontend_image_release_acceptance`. |
| 2026-06-13 | Foundation Alpha POC | `cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55` | [`2026-06-13-211-foundation-alpha-poc-cbbfaff-auth-rbac-smoke.json`](foundation-alpha-poc/cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55/2026-06-13-211-foundation-alpha-poc-cbbfaff-auth-rbac-smoke.json) | Historical 211 Auth/RBAC smoke passed against `ai-platform:cbbfaff-runtime-evidence-root-redaction-v2`; immediately superseded by `ac9a86b` and retained only as reviewed history. |
| 2026-06-13 | Foundation Alpha POC | `cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55` | [`2026-06-13-211-foundation-alpha-poc-cbbfaff-runtime-poc-smoke.json`](foundation-alpha-poc/cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55/2026-06-13-211-foundation-alpha-poc-cbbfaff-runtime-poc-smoke.json) | Historical 211 runtime POC smoke passed against `ai-platform:cbbfaff-runtime-evidence-root-redaction-v2`; immediately superseded by `ac9a86b` and retained only as reviewed history. |
| 2026-06-13 | Foundation Alpha POC | `cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55` | [`2026-06-13-211-foundation-alpha-poc-cbbfaff-governance-runtime-smoke.json`](foundation-alpha-poc/cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55/2026-06-13-211-foundation-alpha-poc-cbbfaff-governance-runtime-smoke.json) | Historical 211 Admin Runtime governance smoke passed against `ai-platform:cbbfaff-runtime-evidence-root-redaction-v2`; immediately superseded by `ac9a86b` and retained only as reviewed history. |
| 2026-06-13 | Foundation Alpha POC | `cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55` | [`2026-06-13-211-foundation-alpha-poc-cbbfaff-release-evidence-runtime-acceptance.json`](foundation-alpha-poc/cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55/2026-06-13-211-foundation-alpha-poc-cbbfaff-release-evidence-runtime-acceptance.json) | Historical 211 release-evidence runtime acceptance passed against `ai-platform:cbbfaff-runtime-evidence-root-redaction-v2`; immediately superseded by `ac9a86b` and retained only as reviewed history. |
| 2026-06-13 | Foundation Alpha POC | `18454a9ccd890dd6b9636a04604b6a100cba31e7` | [`2026-06-13-211-foundation-alpha-poc-18454a9-alert-trace-export-runtime-acceptance.json`](foundation-alpha-poc/18454a9ccd890dd6b9636a04604b6a100cba31e7/2026-06-13-211-foundation-alpha-poc-18454a9-alert-trace-export-runtime-acceptance.json) | Historical 211 alert/trace export runtime acceptance passed against `ai-platform:18454a9-cross-tenant-isolation`; immediately superseded by `cbbfaff` and retained only as reviewed history. |
| 2026-06-13 | Foundation Alpha POC | `18454a9ccd890dd6b9636a04604b6a100cba31e7` | [`2026-06-13-211-foundation-alpha-poc-18454a9-frontend-packaged-runtime-smoke-blocked.json`](foundation-alpha-poc/18454a9ccd890dd6b9636a04604b6a100cba31e7/2026-06-13-211-foundation-alpha-poc-18454a9-frontend-packaged-runtime-smoke-blocked.json) | Historical 211 packaged frontend runtime smoke attempt remained `blocked_environment`; immediately superseded by `cbbfaff` and retained only as blocker history. |
| 2026-06-13 | Foundation Alpha POC | `18454a9ccd890dd6b9636a04604b6a100cba31e7` | [`2026-06-13-211-foundation-alpha-poc-18454a9-auth-rbac-smoke.json`](foundation-alpha-poc/18454a9ccd890dd6b9636a04604b6a100cba31e7/2026-06-13-211-foundation-alpha-poc-18454a9-auth-rbac-smoke.json) | Historical 211 Auth/RBAC smoke passed against `ai-platform:18454a9-cross-tenant-isolation`; immediately superseded by `cbbfaff` and retained only as reviewed history. |
| 2026-06-13 | Foundation Alpha POC | `18454a9ccd890dd6b9636a04604b6a100cba31e7` | [`2026-06-13-211-foundation-alpha-poc-18454a9-runtime-poc-smoke.json`](foundation-alpha-poc/18454a9ccd890dd6b9636a04604b6a100cba31e7/2026-06-13-211-foundation-alpha-poc-18454a9-runtime-poc-smoke.json) | Historical 211 runtime POC smoke passed against `ai-platform:18454a9-cross-tenant-isolation`; immediately superseded by `cbbfaff` and retained only as reviewed history. |
| 2026-06-13 | Foundation Alpha POC | `18454a9ccd890dd6b9636a04604b6a100cba31e7` | [`2026-06-13-211-foundation-alpha-poc-18454a9-governance-runtime-smoke.json`](foundation-alpha-poc/18454a9ccd890dd6b9636a04604b6a100cba31e7/2026-06-13-211-foundation-alpha-poc-18454a9-governance-runtime-smoke.json) | Historical 211 Admin Runtime governance smoke passed against `ai-platform:18454a9-cross-tenant-isolation`; immediately superseded by `cbbfaff` and retained only as reviewed history. |
| 2026-06-13 | Foundation Alpha POC | `18454a9ccd890dd6b9636a04604b6a100cba31e7` | [`2026-06-13-211-foundation-alpha-poc-18454a9-release-evidence-runtime-acceptance.json`](foundation-alpha-poc/18454a9ccd890dd6b9636a04604b6a100cba31e7/2026-06-13-211-foundation-alpha-poc-18454a9-release-evidence-runtime-acceptance.json) | Historical 211 release-evidence runtime acceptance passed against `ai-platform:18454a9-cross-tenant-isolation`; immediately superseded by `cbbfaff` and retained only as reviewed history. |
| 2026-06-12 | Foundation Alpha POC | `d4486ebf5a33ce23a632a69bcf07ef1220b61ea3` | [`2026-06-12-211-foundation-alpha-poc-d4486eb-alert-trace-export-runtime-acceptance.json`](foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-alert-trace-export-runtime-acceptance.json) | Historical 211 alert/trace export runtime acceptance passed against `ai-platform:d4486eb-observability-evidence-loader`; superseded by `18454a9` and then `cbbfaff`, retained only as reviewed history. |
| 2026-06-12 | Foundation Alpha POC | `d4486ebf5a33ce23a632a69bcf07ef1220b61ea3` | [`2026-06-12-211-foundation-alpha-poc-d4486eb-frontend-packaged-runtime-smoke-blocked.json`](foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-frontend-packaged-runtime-smoke-blocked.json) | Historical 211 packaged frontend runtime smoke attempt remained `blocked_environment`; superseded by `18454a9` and then `cbbfaff`, retained as blocker history only and does not close `packaged_frontend_image_release_acceptance`. |
| 2026-06-12 | Foundation Alpha POC | `d4486ebf5a33ce23a632a69bcf07ef1220b61ea3` | [`2026-06-12-211-foundation-alpha-poc-d4486eb-auth-rbac-smoke.json`](foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-auth-rbac-smoke.json) | Historical 211 Auth/RBAC smoke passed against `ai-platform:d4486eb-observability-evidence-loader`; superseded by `18454a9` and then `cbbfaff`, retained only as reviewed history. |
| 2026-06-12 | Foundation Alpha POC | `d4486ebf5a33ce23a632a69bcf07ef1220b61ea3` | [`2026-06-12-211-foundation-alpha-poc-d4486eb-runtime-poc-smoke.json`](foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-runtime-poc-smoke.json) | Historical 211 runtime POC smoke passed against `ai-platform:d4486eb-observability-evidence-loader`; superseded by `18454a9` and then `cbbfaff`, retained only as reviewed history. |
| 2026-06-12 | Foundation Alpha POC | `d4486ebf5a33ce23a632a69bcf07ef1220b61ea3` | [`2026-06-12-211-foundation-alpha-poc-d4486eb-governance-runtime-smoke.json`](foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-governance-runtime-smoke.json) | Historical 211 Admin Runtime governance smoke passed against `ai-platform:d4486eb-observability-evidence-loader`; superseded by `18454a9` and then `cbbfaff`, retained only as reviewed history. |
| 2026-06-12 | Foundation Alpha POC | `d4486ebf5a33ce23a632a69bcf07ef1220b61ea3` | [`2026-06-12-211-foundation-alpha-poc-d4486eb-release-evidence-runtime-acceptance.json`](foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-release-evidence-runtime-acceptance.json) | Historical 211 release-evidence runtime acceptance passed against `ai-platform:d4486eb-observability-evidence-loader`; superseded by `18454a9` and then `cbbfaff`, retained only as reviewed history. |
| 2026-06-12 | Foundation Alpha POC | `00e4e6b950709439850749fe26af9c0943f6a07c` | [`2026-06-12-211-foundation-alpha-poc-00e4e6b-alert-trace-export-runtime-acceptance.json`](foundation-alpha-poc/00e4e6b950709439850749fe26af9c0943f6a07c/2026-06-12-211-foundation-alpha-poc-00e4e6b-alert-trace-export-runtime-acceptance.json) | Historical 211 alert/trace export runtime acceptance passed against `ai-platform:00e4e6b-skill-release-evidence`; superseded by `d4486eb` and retained only as reviewed history. |
| 2026-06-12 | Foundation Alpha POC | `00e4e6b950709439850749fe26af9c0943f6a07c` | [`2026-06-12-211-foundation-alpha-poc-00e4e6b-frontend-packaged-runtime-smoke-blocked.json`](foundation-alpha-poc/00e4e6b950709439850749fe26af9c0943f6a07c/2026-06-12-211-foundation-alpha-poc-00e4e6b-frontend-packaged-runtime-smoke-blocked.json) | Historical 211 packaged frontend runtime smoke attempt remained `blocked_environment`; superseded by `d4486eb` and retained only as reviewed blocker history. |
| 2026-06-12 | Foundation Alpha POC | `00e4e6b950709439850749fe26af9c0943f6a07c` | [`2026-06-12-211-foundation-alpha-poc-00e4e6b-auth-rbac-smoke.json`](foundation-alpha-poc/00e4e6b950709439850749fe26af9c0943f6a07c/2026-06-12-211-foundation-alpha-poc-00e4e6b-auth-rbac-smoke.json) | Historical 211 Auth/RBAC smoke passed against `ai-platform:00e4e6b-skill-release-evidence`; superseded by `d4486eb` and retained only as reviewed history. |
| 2026-06-12 | Foundation Alpha POC | `00e4e6b950709439850749fe26af9c0943f6a07c` | [`2026-06-12-211-foundation-alpha-poc-00e4e6b-runtime-poc-smoke.json`](foundation-alpha-poc/00e4e6b950709439850749fe26af9c0943f6a07c/2026-06-12-211-foundation-alpha-poc-00e4e6b-runtime-poc-smoke.json) | Historical 211 runtime POC smoke passed against `ai-platform:00e4e6b-skill-release-evidence`; superseded by `d4486eb` and retained only as reviewed history. |
| 2026-06-12 | Foundation Alpha POC | `00e4e6b950709439850749fe26af9c0943f6a07c` | [`2026-06-12-211-foundation-alpha-poc-00e4e6b-governance-runtime-smoke.json`](foundation-alpha-poc/00e4e6b950709439850749fe26af9c0943f6a07c/2026-06-12-211-foundation-alpha-poc-00e4e6b-governance-runtime-smoke.json) | Historical 211 Admin Runtime governance smoke passed against `ai-platform:00e4e6b-skill-release-evidence`; superseded by `d4486eb` and retained only as reviewed history. |
| 2026-06-12 | Foundation Alpha POC | `00e4e6b950709439850749fe26af9c0943f6a07c` | [`2026-06-12-211-foundation-alpha-poc-00e4e6b-release-evidence-runtime-acceptance.json`](foundation-alpha-poc/00e4e6b950709439850749fe26af9c0943f6a07c/2026-06-12-211-foundation-alpha-poc-00e4e6b-release-evidence-runtime-acceptance.json) | Historical 211 release-evidence runtime acceptance passed against `ai-platform:00e4e6b-skill-release-evidence`; superseded by `d4486eb` and retained only as reviewed history. |
| 2026-06-12 | Foundation Alpha POC | `6088d5d179c422a6d753e1b77079410503e58925` | [`2026-06-12-211-foundation-alpha-poc-6088d5d-alert-trace-export-runtime-acceptance.json`](foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-alert-trace-export-runtime-acceptance.json) | Historical 211 alert/trace export runtime acceptance passed inside `ai-platform:6088d5d-alert-trace-acceptance`; supports removing the alert/trace 211 acceptance blocker for this runtime subject but does not enable alert delivery, close G9, close signed Skill package/SBOM, close packaged frontend image release acceptance, or close G0 source-authority parity while labels still record the old external env-file layout. |
| 2026-06-12 | Foundation Alpha POC | `6088d5d179c422a6d753e1b77079410503e58925` | [`2026-06-12-211-foundation-alpha-poc-6088d5d-frontend-packaged-runtime-smoke-blocked.json`](foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-frontend-packaged-runtime-smoke-blocked.json) | Historical 211 packaged frontend runtime smoke attempt was reviewed and classified as `blocked_environment` because the Docker-capable host cannot pull the required `node:22-alpine` and `nginx:1.27-alpine` base images and has no target frontend image cached; this is blocker evidence only and does not close `packaged_frontend_image_release_acceptance`. |
| 2026-06-12 | Foundation Alpha POC | `6088d5d179c422a6d753e1b77079410503e58925` | [`2026-06-12-211-foundation-alpha-poc-6088d5d-auth-rbac-smoke.json`](foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-auth-rbac-smoke.json) | Historical 211 Auth/RBAC smoke passed against `ai-platform:6088d5d-alert-trace-acceptance`; supports the 6088d5d Foundation Alpha POC security baseline but does not close production auth rollout or G0 source-authority parity while labels still record the old external env-file layout. |
| 2026-06-12 | Foundation Alpha POC | `6088d5d179c422a6d753e1b77079410503e58925` | [`2026-06-12-211-foundation-alpha-poc-6088d5d-runtime-poc-smoke.json`](foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-runtime-poc-smoke.json) | Historical 211 runtime POC smoke passed against `ai-platform:6088d5d-alert-trace-acceptance`; verifies the controlled POC loop and context public projection for this runtime subject but does not close packaged frontend image, signed Skill package/SBOM, production, or G0 source-authority parity gates. |
| 2026-06-12 | Foundation Alpha POC | `6088d5d179c422a6d753e1b77079410503e58925` | [`2026-06-12-211-foundation-alpha-poc-6088d5d-governance-runtime-smoke.json`](foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-governance-runtime-smoke.json) | Historical 211 Admin Runtime governance smoke passed against `ai-platform:6088d5d-alert-trace-acceptance`; supports the current POC governance projection but does not close signed Skill package/SBOM, dependency/license, visual dashboard, production governance rollout, or G0 source-authority parity gates. |
| 2026-06-12 | Foundation Alpha POC | `6088d5d179c422a6d753e1b77079410503e58925` | [`2026-06-12-211-foundation-alpha-poc-6088d5d-release-evidence-runtime-acceptance.json`](foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-release-evidence-runtime-acceptance.json) | Historical 211 release-evidence runtime acceptance passed inside `ai-platform:6088d5d-alert-trace-acceptance`; supports removing the runtime export/retention blocker for this runtime subject but does not close signed Skill package/SBOM, packaged frontend image, production, or G0 source-authority parity gates. |
| 2026-06-12 | Foundation Alpha POC | `b96d02e232176bade455f2af2bc3080f8f372206` | [`2026-06-12-211-foundation-alpha-poc-b96d02e-release-evidence-runtime-acceptance.json`](foundation-alpha-poc/b96d02e232176bade455f2af2bc3080f8f372206/2026-06-12-211-foundation-alpha-poc-b96d02e-release-evidence-runtime-acceptance.json) | Historical 211 release-evidence runtime acceptance passed inside `ai-platform:b96d02e-release-evidence-runtime-acceptance`; superseded by `00e4e6b`. |
| 2026-06-12 | Foundation Alpha POC | `b96d02e232176bade455f2af2bc3080f8f372206` | [`2026-06-12-211-foundation-alpha-poc-b96d02e-governance-runtime-smoke.json`](foundation-alpha-poc/b96d02e232176bade455f2af2bc3080f8f372206/2026-06-12-211-foundation-alpha-poc-b96d02e-governance-runtime-smoke.json) | Historical 211 Admin Runtime governance smoke passed against `ai-platform:b96d02e-release-evidence-runtime-acceptance`; superseded by `00e4e6b`. |
| 2026-06-12 | Foundation Alpha POC | `b96d02e232176bade455f2af2bc3080f8f372206` | [`2026-06-12-211-foundation-alpha-poc-b96d02e-auth-rbac-smoke.json`](foundation-alpha-poc/b96d02e232176bade455f2af2bc3080f8f372206/2026-06-12-211-foundation-alpha-poc-b96d02e-auth-rbac-smoke.json) | Historical 211 Auth/RBAC smoke passed against `ai-platform:b96d02e-release-evidence-runtime-acceptance`; superseded by `00e4e6b`. |
| 2026-06-12 | Foundation Alpha POC | `b96d02e232176bade455f2af2bc3080f8f372206` | [`2026-06-12-211-foundation-alpha-poc-b96d02e-runtime-poc-smoke.json`](foundation-alpha-poc/b96d02e232176bade455f2af2bc3080f8f372206/2026-06-12-211-foundation-alpha-poc-b96d02e-runtime-poc-smoke.json) | Historical 211 runtime smoke passed for the controlled context public projection and release-evidence-runtime-acceptance rollout slice; superseded by `00e4e6b`. |
| 2026-06-12 | Foundation Alpha POC | `948179c73734aa61ed764fb3485f5415fca8f193` | [`2026-06-12-211-foundation-alpha-poc-948179c-governance-runtime-smoke.json`](foundation-alpha-poc/948179c73734aa61ed764fb3485f5415fca8f193/2026-06-12-211-foundation-alpha-poc-948179c-governance-runtime-smoke.json) | Historical 211 Admin Runtime governance smoke passed against `ai-platform:948179c-skill-release-scaffold`; superseded by `b96d02e`. |
| 2026-06-12 | Foundation Alpha POC | `948179c73734aa61ed764fb3485f5415fca8f193` | [`2026-06-12-211-foundation-alpha-poc-948179c-auth-rbac-smoke.json`](foundation-alpha-poc/948179c73734aa61ed764fb3485f5415fca8f193/2026-06-12-211-foundation-alpha-poc-948179c-auth-rbac-smoke.json) | Historical 211 Auth/RBAC smoke passed against `ai-platform:948179c-skill-release-scaffold`; superseded by `b96d02e`. |
| 2026-06-12 | Foundation Alpha POC | `948179c73734aa61ed764fb3485f5415fca8f193` | [`2026-06-12-211-foundation-alpha-poc-948179c-skill-release-scaffold-smoke.json`](foundation-alpha-poc/948179c73734aa61ed764fb3485f5415fca8f193/2026-06-12-211-foundation-alpha-poc-948179c-skill-release-scaffold-smoke.json) | Historical 211 runtime smoke passed for the controlled context public projection and skill-release-scaffold rollout slice; superseded by `b96d02e`. |
| 2026-06-12 | Foundation Alpha POC | `b7689d0cbc6fa3913de47aea3aded1036f0ea0ae` | [`2026-06-12-211-foundation-alpha-poc-b7689d0-auth-rbac-smoke.json`](foundation-alpha-poc/b7689d0cbc6fa3913de47aea3aded1036f0ea0ae/2026-06-12-211-foundation-alpha-poc-b7689d0-auth-rbac-smoke.json) | Historical 211 Auth/RBAC smoke passed against `ai-platform:b7689d0-context-public-projection-v2`; superseded by `b96d02e`. |
| 2026-06-12 | Foundation Alpha POC | `b7689d0cbc6fa3913de47aea3aded1036f0ea0ae` | [`2026-06-12-211-foundation-alpha-poc-b7689d0-context-public-projection-smoke.json`](foundation-alpha-poc/b7689d0cbc6fa3913de47aea3aded1036f0ea0ae/2026-06-12-211-foundation-alpha-poc-b7689d0-context-public-projection-smoke.json) | Historical 211 runtime smoke passed for the controlled context public projection slice; context public projection returned `summary_source=chat_stream` and safe `input_keys=["attachments","message"]`; superseded by `b96d02e`. |
| 2026-06-12 | Foundation Alpha POC | `2384e19dcac2e39fbcf9c27dc990f5774d391422` | [`2026-06-12-211-foundation-alpha-poc-2384e19-governance-runtime-smoke.json`](foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-governance-runtime-smoke.json) | Historical 211 Admin Runtime governance smoke passed against `ai-platform:2384e19-context-source-provenance` using operator verifier source `8206690`; G6 remains partial and not production gate closure. |
| 2026-06-12 | Foundation Alpha POC | `2384e19dcac2e39fbcf9c27dc990f5774d391422` | [`2026-06-12-211-foundation-alpha-poc-2384e19-context-source-provenance-auth-rbac-smoke.json`](foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-context-source-provenance-auth-rbac-smoke.json) | Historical 211 Auth/RBAC smoke passed against `ai-platform:2384e19-context-source-provenance`; superseded by `b7689d0`. |
| 2026-06-12 | Foundation Alpha POC | `2384e19dcac2e39fbcf9c27dc990f5774d391422` | [`2026-06-12-211-foundation-alpha-poc-2384e19-context-source-provenance-smoke.json`](foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-context-source-provenance-smoke.json) | Historical 211 runtime smoke passed for the controlled context snapshot source-provenance slice; superseded by `b7689d0`. |
| 2026-06-12 | Foundation Alpha POC | `e274d78b21c22fdf4f56a8cf8b31a0480d42c22f` | [`2026-06-12-211-foundation-alpha-poc-e274d78-auth-rbac-smoke.json`](foundation-alpha-poc/e274d78b21c22fdf4f56a8cf8b31a0480d42c22f/2026-06-12-211-foundation-alpha-poc-e274d78-auth-rbac-smoke.json) | Historical 211 Auth/RBAC smoke passed against `ai-platform:e274d78-g9-runtime-readiness-tools`; superseded by `2384e19` and `b7689d0`. |
| 2026-06-12 | Foundation Alpha POC | `e274d78b21c22fdf4f56a8cf8b31a0480d42c22f` | [`2026-06-12-211-foundation-alpha-poc-e274d78-runtime-readiness-tools-smoke.json`](foundation-alpha-poc/e274d78b21c22fdf4f56a8cf8b31a0480d42c22f/2026-06-12-211-foundation-alpha-poc-e274d78-runtime-readiness-tools-smoke.json) | Historical 211 runtime smoke passed for the controlled Foundation Alpha POC loop and container-side release-evidence export acceptance preflight against `ai-platform:e274d78-g9-runtime-readiness-tools`; superseded by `2384e19` and `b7689d0`. |
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
- `alert_trace_export_runtime_acceptance`

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

## Runtime Acceptance Verifier

`tools/verify_release_evidence_runtime_acceptance.py` verifies a
runtime-packaged `docs/release-evidence/` tree can emit a safe reviewed index
and that the retention policy still requires reviewed, redacted, delete-safe
handling. The verifier emits
`ai-platform.release-evidence-runtime-acceptance.v1` with safe counts and
policy status only; it does not export raw runtime payloads, `source_ref`,
`evidence_ref`, storage keys, sandbox paths, or secret-like values.

`tools/foundation_alpha_readiness.py` may consume this result only when it is
wrapped in a reviewed, redacted `ai-platform.release-evidence-entry.v1` 211
runtime-smoke evidence entry for the same runtime subject. A local verifier
pass is a preflight and does not by itself close the G9 release-evidence
runtime blocker.

## Open Gaps

- `signed_skill_package_or_sbom_review_evidence`
- `g9_admin_runtime_observability_partial_followups_open`
- `packaged_frontend_image_release_acceptance`

The 2026-06-13 `ac9a86b` 211 evidence records reviewed runtime export,
retention, alert/trace export runtime acceptance, and cross-tenant artifact
isolation for the active Foundation Alpha POC runtime subject. This removes the narrower
`g9_runtime_export_and_retention_acceptance` and
`alert_delivery_and_trace_export_211_acceptance` blockers for that runtime
subject. The superseded `cbbfaff` refresh still records packaged frontend
blocker evidence as `blocked_environment`; it does not close
`packaged_frontend_image_release_acceptance`. The ac9a86b refresh does not close
G9 or production readiness by itself: signed Skill package or SBOM review
evidence, packaged frontend image release acceptance, G9 Admin Runtime
observability follow-ups, G0 source-authority parity, and production auth
rollout remain separate blockers.
