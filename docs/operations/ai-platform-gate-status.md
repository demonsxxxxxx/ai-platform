# ai-platform Gate Status Snapshot

Date: 2026-06-12

This snapshot keeps the current PRD, foundation roadmap, guardrails, repository
code, 211 runtime, and issue-driven priorities aligned. It is not automatic
gate-closure evidence and must not be used to auto-close GitHub issues. Gate
closure still requires the
issue -> PR -> review -> merge -> 211 deploy/smoke -> close issue workflow in
`docs/agent-rules/github-issue-pr-workflow.md`.

## Foundation Alpha POC Smoke

Status: `211 verified for Foundation Alpha POC`, not production gate closure.

Generate the current operator readiness summary with:

```powershell
python tools/foundation_alpha_readiness.py --format json
python tools/foundation_alpha_readiness.py --format markdown
```

The operator summary must be read through `runtime_source_relation`. When the
source tree is newer than the verified running API/worker image, it reports
`source_synced_runtime_pending` and sets
`current_source_verified_by_running_runtime=false`. That state means the
controlled POC loop evidence remains useful for the recorded runtime subject,
but current source commits still need rollout and smoke evidence before being
treated as runtime-verified.

When it reports `runtime_current_for_runtime_relevant_source`, the latest
running runtime subject still covers all runtime-affecting source. This state is
only valid when `runtime_affecting_dirty_paths` and
`runtime_affecting_changes_since_runtime_subject` are empty. In that case,
docs/tests/evidence/readiness record changes may be newer than the image, so
`current_source_exact_runtime_commit_match=false` can coexist with
`current_source_verified_by_running_runtime=true`.

The 211 source directory is often a synced archive rather than a Git worktree.
For source-only docs/tests/evidence syncs, write a local-only
`.ai-platform-source-snapshot.json` next to `.ai-platform-source-revision` so
`tools/foundation_alpha_readiness.py` can prove the runtime-affecting delta is
empty. Missing, stale, or malformed snapshot markers intentionally fail closed.

On 2026-06-12, PR #28 runtime subject commit
`8d61fd7cd8de8ec1cd99ce7e813a1431f9b672bf` was synced to the 211 runtime
subject and the 211 API and worker ran
`ai-platform:8d61fd7-context-projection-fixed` with image ID
`sha256:b2c09010fe5dd433627004d74e1e0bbb048fd0d5aa0c3cb28017d8712abb6d17`.
Both runtime source labels and in-container source markers pointed to
`8d61fd7cd8de8ec1cd99ce7e813a1431f9b672bf`. Runtime labels pointed to the
repo-local compose file under
`/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform`,
while the existing runtime env file still lived outside that repo-local deploy
directory and was passed as an external env source without copying or printing
secret values. API health returned `ok`.

The immediately superseded runtime subject commit was
`458f6056dd0fa533162e780a303d79ce1b3d0eec`; it ran
`ai-platform:458f605-auth-rbac-redaction` with
image ID
`sha256:a91b3d1c62aacb4d52604e659d9e6ea30c1a96e7669547ba63e211f976554c9e`.
Both superseded runtime source labels pointed to
`458f6056dd0fa533162e780a303d79ce1b3d0eec`. Runtime labels pointed to the
repo-local compose file under
`/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform`,
API health returned `ok`, and OpenAPI exposed
`/api/ai/artifacts/{artifact_id}/preview`.

The aggregate verifier `tools/verify_poc_gate.py` returned `ok: true` on 211
for the controlled POC loop: LambChat thin-shell frontend, same-origin API
health, public/admin projection boundary, company auth bridge, general chat run,
document review attachment run, artifact download isolation, artifact preview
isolation, playback with preview URL and no private payload leakage, company
login audit, Admin capacity/backpressure fields, and context snapshot public
projection without raw material IDs. The refreshed PR #28 smoke includes the
machine-verifiable context public summary fields: `input_keys`, memory policy
source, long-term-memory flag, execution tier, and generated-at presence. Older
smoke records without these fields still fail closed as
`context_snapshot_public_projection_followup_required`, and records without any
runtime projection still fail closed as `missing_context_snapshot_public_projection`.
The current reviewed, redacted
release-evidence entry is
`docs/release-evidence/foundation-alpha-poc/8d61fd7cd8de8ec1cd99ce7e813a1431f9b672bf/2026-06-12-211-foundation-alpha-poc-8d61fd7-smoke.json`.

The focused Auth/RBAC verifier `tools/verify_auth_rbac_smoke.py` also returned
`ok: true` on 211 against the same runtime. The refreshed 2026-06-12 02:21
+08:00 smoke used runtime subject `8d61fd7`. It verified unauthenticated `/api/auth/me`
returns 401, platform `/api/ai/auth/me` returns the trusted principal with
tenant match, invalid gateway secret access to `/api/ai/auth/me` fails with
403, ordinary trusted principals are denied from Admin Runtime with 403, admin
trusted principals can read the required same-tenant Admin Runtime sections with
200, and the projection scan did not find private or secret-like values. The
current reviewed, redacted Auth/RBAC evidence entry is
`docs/release-evidence/foundation-alpha-poc/8d61fd7cd8de8ec1cd99ce7e813a1431f9b672bf/2026-06-12-211-foundation-alpha-poc-8d61fd7-auth-rbac-smoke.json`.

Earlier smoke evidence for
`458f6056dd0fa533162e780a303d79ce1b3d0eec`,
`9b02836262fb0f238a7f90b9705bf39a8b298158`,
`cdc09ba8867d91e8db76570fbf158e6d082da7cf`,
`8f454696be0e9c532fa86bc61ef353e4d3dec4f8`,
`faa7ad6aa61637cbcdf3a22ce81de119762e96bf`,
`a3f1d739e12686cba2e0b309de26a4e1127bd3a5`,
`8c0cffca63bc747fad0a5771f209acc8a608ab9e`,
`bf20432f9889efa8b367afdf512c641068ba30bc`, and
`3874281276c84a418bd08bda56d7ea55b52970b7` remains retained as historical evidence only; the PR #28 evidence above is the active Foundation Alpha POC reference for this branch. The previously superseded runtime image was `ai-platform:9b02836-context-output`.

This smoke does not close #21 capacity, G7 Docker sandbox hardening, ordinary
user multi-agent exposure, department rollout, release-evidence runtime export,
release-evidence retention runtime acceptance, or packaged frontend image
release acceptance.

## Current Gate Status

| Gate | Current status | Evidence now in repository | Remaining blocker before closure |
| --- | --- | --- | --- |
| G0-G1 Source Authority / Security Baseline | Foundation Alpha POC has fresh 211 source/deploy/runtime-label parity, company-login audit evidence, and focused Auth/RBAC smoke evidence; keep under regression. | PRD v2, tech acceptance matrix, roadmap, guardrails, source-authority tests, repo-local compose context, frontend source migration, redacted deploy templates, and 2026-06-12 POC release evidence. | Full issue/PR/review closure path and broader auth/session/RBAC/tenant/redaction regression are still required before production closure. |
| G2-G4 Control Plane MVP | Substantial coverage; keep under regression. | Session/run/file/artifact/skill/tool/memory/event/audit contracts, repositories, routes, schema indexes, and focused tests. | Full regression before PR/deploy, plus no executor-owned platform schema drift. |
| G5 Run Lifecycle / Worker Runtime V1 | Foundation Alpha POC verified queue/run/worker execution and Admin capacity/backpressure projection; not capacity-closed. | Tenant-aware queue lease, worker maintenance, active-run admission, bounded metadata, Admin Runtime capacity/backpressure projection, #20 roadmap closure notes, and 2026-06-12 POC verifier evidence. | #21 remains open: large queue bounded lookup pressure, multi-tenant load, and recorded seven-gate load evidence are still missing. Production defaults stay unchanged. |
| G6 Tool / Skill / Memory Governance | POC governance baseline is visible, but ordinary-user expansion remains blocked. | Tool policy taxonomy/history, public permission-card projection, skill release/dependency policy contracts, memory delete/retention/redaction/export readiness, office context-pack architecture readiness, context snapshot public provenance projection contract, governance readiness CLI, and POC runs using governed skills. | Legacy frontend route remap/policy enforcement, signed package or SBOM review evidence, dependency vulnerability/license evidence, context-pack persistence/executor injection/frontend provenance acceptance, runtime/Admin dashboard acceptance, and broader 211 acceptance. |
| G7 Sandbox / Resource Hardening | Blocked for high-risk expansion. | Fake provider remains local/test-only; capacity docs expose sandbox limits and missing hardening warnings. | Docker provider hardening, egress/quota policy, orphan cleanup, container security options, and Docker-capable 211 smoke. |
| G8 Multi-Agent Controlled Beta | Feature-flagged only. | Dispatcher and child-run admission work exists behind current controls. | Tenant-aware scheduling quota/backpressure, #21 capacity evidence, observability, sandbox, and tool governance gates must pass before ordinary-user exposure. |
| G9 Observability / Quality / Ops | Foundation Alpha POC release evidence exists; G9 remains partial. | Admin Runtime overview, capacity/governance/observability readiness docs and tools, error taxonomy/dashboard contracts, release-evidence contracts, trace/audit export contracts, frontend projection audit, and reviewed 211 POC smoke entry. | Runtime dashboard acceptance, recorded capacity evidence, model-gateway backpressure evidence, golden-set eval runtime, alert delivery/runtime calibration, trace/export 211 acceptance, and release-evidence runtime export/retention acceptance. |
| G10 Internal Beta / Department Rollout | Blocked. | Candidate internal workflows are named only as examples in roadmap. | Select 1-2 real internal workflow owners, complete prior gates, record cost/quality/audit/rollback evidence, and pass 211 acceptance. |

## Issue-Driven Thin Spots

| Issue area | Current judgment | Next closure action |
| --- | --- | --- |
| #17 frontend source migration | Source lives under `frontend/web` with projection audit, `ci:verify`, release traceability, GitHub Actions workflow, packaged frontend image definition, and 211 thin-shell POC smoke. | Run or refresh frontend install/lint/build when changing browser code; complete packaged frontend image smoke/release acceptance on 211 or another Docker-capable host. |
| #21 capacity baseline | Baseline plan, snapshot/verdict/profile tools, bounded probe harness, and Admin Runtime capacity/backpressure visibility exist; bounded probes now fail closed when successful Admin Runtime overview responses miss required baseline sections. | Record approved load evidence for the seven gates before raising any production default. Until then every profile remains `do_not_raise_without_recorded_load_test_evidence`. |
| G6 governance | Source-level policies and readiness contracts exist. | Convert contracts into runtime/Admin dashboard acceptance and real reviewed Skill release evidence; keep long-term memory fail-closed. |
| G8/G10 expansion | Not a current implementation target. | Keep feature flags and do not broaden ordinary-user multi-agent exposure until G5/G6/G7/G9 gates are closed. |

## Frontend Projection Boundary

Browser-side code must consume same-origin ai-platform public or same-tenant
admin projections only. It must not read executor private payloads, raw storage
keys, sandbox work directories, raw runtime paths, secret-like values, raw
request payloads, raw decision payloads, or raw Skill staging paths. The active
projection audit and `frontend/web` CI gate are the repository-owned checks for
this boundary.

## Capacity Decision Boundary

Current defaults must not be increased without recorded load-test evidence.
The capacity tooling may generate baselines, dry-run plans, bounded probes,
evidence bundles, and fail-closed verdicts, but probes are not accepted as
recorded gate evidence until an operator-reviewed recorded gate snapshot
contains measured results, cleanup proof, stop-condition status, and deployed
commit binding.
Bounded probes are allowed to fail closed on missing Admin Runtime projection
sections; that improves operator safety but still does not count as recorded
load-test evidence.
