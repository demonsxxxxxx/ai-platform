# ai-platform Gate Status Snapshot

Date: 2026-06-12

This snapshot keeps the current PRD, foundation roadmap, guardrails, repository
code, 211 runtime, and issue-driven priorities aligned. It is not automatic
gate-closure evidence and must not be used to auto-close GitHub issues. Gate
closure still requires the
issue -> PR -> review -> merge -> 211 deploy/smoke -> close issue workflow in
`docs/agent-rules/github-issue-pr-workflow.md`.

## Foundation Alpha POC Smoke

Status: historical 211 POC smoke reviewed; current context public-summary
contract refresh required, not production gate closure.

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
`runtime_relevant_source_verified_by_running_runtime=true`. It must not be read
as exact current-source runtime verification:
`current_source_verified_by_running_runtime` and
`controlled_poc_loop_verified_for_current_source` stay false until the running
image matches the current source tree.

The 211 source directory is often a synced archive rather than a Git worktree.
For source-only docs/tests/evidence syncs, write a local-only
`.ai-platform-source-snapshot.json` next to `.ai-platform-source-revision` so
`tools/foundation_alpha_readiness.py` can prove the runtime-affecting delta is
empty. Missing, stale, or malformed snapshot markers intentionally fail closed.

On 2026-06-12, runtime subject commit
`2384e19dcac2e39fbcf9c27dc990f5774d391422` was synced to the 211 runtime
subject and the 211 API and worker ran
`ai-platform:2384e19-context-source-provenance` with image ID
`sha256:2fde0184a1212332eeb15ff657b9a82ac96575a450becd6ac190ad22f8d589a4`.
Both runtime source labels pointed to
`2384e19dcac2e39fbcf9c27dc990f5774d391422`. Runtime labels pointed to the
repo-local 211 deploy composition, API health returned `ok`, container-side
`python -m compileall -q app tools scripts` passed for API and worker, and the
aggregate verifier `tools/verify_poc_gate.py` returned `ok: true` on 211 for
the controlled context source-provenance slice. That smoke verified
`summary_source=chat_stream`, safe `input_keys=["message"]`, memory policy
source, execution tier, generated-at presence, and no raw material IDs or
forbidden projection leaks. A later source-level contract tightened file-context
provenance: when `file_count > 0`, public `input_keys` must include the safe
`attachments` signal. Therefore this older 211 evidence remains reviewed
historical proof for the controlled loop, but it no longer closes the current
context public-summary verifier until the 211 smoke is rerun with
`input_keys=["attachments","message"]` or equivalent. The reviewed, redacted
release-evidence entry is
`docs/release-evidence/foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-context-source-provenance-smoke.json`.

The focused Auth/RBAC verifier `tools/verify_auth_rbac_smoke.py` also returned
`ok: true` on 211 against `2384e19`. It verified unauthenticated auth rejection,
trusted platform principal projection, invalid gateway secret rejection,
ordinary-user Admin Runtime denial, and same-tenant Admin Runtime access for an
admin smoke principal. The reviewed, redacted Auth/RBAC evidence entry is
`docs/release-evidence/foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-context-source-provenance-auth-rbac-smoke.json`.

The focused governance verifier
`tools/verify_governance_runtime_smoke.py` returned `ok: true` on 211 against
the same running runtime subject. It verified ordinary-user Admin Runtime denial,
same-tenant admin access, G6 governance schema
`ai-platform.governance-readiness.v1`, required tool/skill/memory governance
domains, tool policy taxonomy and bulk-review signals, skill release/dashboard
signals with `dashboard_contract` trimmed from the overview projection, memory
fail-closed/context-provenance/office-context signals, and no forbidden
projection terms in the reviewed summary. The verifier source was synced at
`820669037978237182ecd2fd27c2ffa10a953c0b`; the API/worker runtime image
remained `ai-platform:2384e19-context-source-provenance`, and the synced source
snapshot declared no runtime-affecting delta from `2384e19`. The reviewed,
redacted governance smoke evidence entry is
`docs/release-evidence/foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-governance-runtime-smoke.json`.

This 2384e19 rollout used a runtime-only image rebased from the previous
healthy image because a full Docker build stalled on dependency installation.
Treat that as a deployment workaround, not the preferred release path. The
repo-local 211 deploy directory still lacks a committed or copied real `.env`;
compose used the existing external runtime env path without printing or copying
secret values.

Immediately before that slice, runtime subject commit
`e274d78b21c22fdf4f56a8cf8b31a0480d42c22f` was synced to the 211 runtime
subject and the 211 API and worker ran
`ai-platform:e274d78-g9-runtime-readiness-tools` with image ID
`sha256:a8873641808cbf15f919a12a2d4a540a2cbf309557a15f8f832e0dbb0801f4ab`.
Both runtime source labels pointed to
`e274d78b21c22fdf4f56a8cf8b31a0480d42c22f`. Runtime labels pointed to the
repo-local 211 deploy composition, API health returned `ok`, container-side
`python -m compileall -q app tools scripts` passed for API and worker, and the container-side
`tools/release_evidence_export_acceptance.py` preflight returned
`ready_for_operator_review` with `safe_entry_count=16`, `blocked_entry_count=0`,
and `excluded_entry_count=3`.

The aggregate verifier `tools/verify_poc_gate.py` returned `ok: true` on 211
for the controlled POC loop: LambChat thin-shell frontend, same-origin API
health, public/admin projection boundary, company auth bridge, general chat run,
document review attachment run, artifact download isolation, artifact preview
isolation, playback with preview URL and no private payload leakage, company
login audit, Admin capacity/backpressure fields, and context snapshot public
projection with `summary_source=chat_stream`, safe `input_keys=["message"]`,
memory policy source, execution tier, generated-at presence, and no raw
material IDs. The current verifier now also requires `attachments` in
`input_keys` whenever `file_count > 0`, so this historical smoke must be
refreshed before it can satisfy the current context public-summary contract.
`tools/foundation_alpha_readiness.py` promotes that context projection into the
G6 evidence summary and fails closed as
`missing_context_snapshot_public_projection` when an older smoke record lacks
it, or as `attachments_input_key` when file-context provenance lacks the
attachment signal. The reviewed, redacted release-evidence entry is
`docs/release-evidence/foundation-alpha-poc/e274d78b21c22fdf4f56a8cf8b31a0480d42c22f/2026-06-12-211-foundation-alpha-poc-e274d78-runtime-readiness-tools-smoke.json`.

The focused Auth/RBAC verifier `tools/verify_auth_rbac_smoke.py` also returned
`ok: true` on 211 against the same runtime. The refreshed 2026-06-12 smoke used
runtime subject `e274d78`. It verified unauthenticated `/api/auth/me`
returns 401, platform `/api/ai/auth/me` returns the trusted principal with
tenant match, invalid gateway secret access to `/api/ai/auth/me` fails with
403, ordinary trusted principals are denied from Admin Runtime with 403, admin
trusted principals can read the required same-tenant Admin Runtime sections with
200, and the projection scan did not find private or secret-like values. The
PR #26 verifier fix allows legitimate Admin Runtime observability/readiness
metric text such as token/cost/error summaries while continuing to fail closed
on secret-like keys and credential-shaped values. The
reviewed, redacted Auth/RBAC evidence entry is
`docs/release-evidence/foundation-alpha-poc/e274d78b21c22fdf4f56a8cf8b31a0480d42c22f/2026-06-12-211-foundation-alpha-poc-e274d78-auth-rbac-smoke.json`.

Earlier smoke evidence for
`e274d78b21c22fdf4f56a8cf8b31a0480d42c22f`,
`a63dbbd0b474cce3702b3485e6589f86155cf5aa`,
`d95107da2b5691781518bdbb8c4e5e76409869f3`,
`458f6056dd0fa533162e780a303d79ce1b3d0eec`,
`9b02836262fb0f238a7f90b9705bf39a8b298158`,
`cdc09ba8867d91e8db76570fbf158e6d082da7cf`,
`8f454696be0e9c532fa86bc61ef353e4d3dec4f8`,
`faa7ad6aa61637cbcdf3a22ce81de119762e96bf`,
`a3f1d739e12686cba2e0b309de26a4e1127bd3a5`,
`8c0cffca63bc747fad0a5771f209acc8a608ab9e`,
`bf20432f9889efa8b367afdf512c641068ba30bc`, and
`3874281276c84a418bd08bda56d7ea55b52970b7` remains retained as historical evidence only; the current-main evidence above is the active Foundation Alpha POC reference.
The immediately superseded runtime image was `ai-platform:3ead61c-g9-release-evidence-runtime-docs`.

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
| G6 Tool / Skill / Memory Governance | Admin Runtime governance projection now has focused 211 smoke evidence for the POC runtime, but G6 remains partial and ordinary-user expansion remains blocked. | Tool policy taxonomy/history, public permission-card projection, skill release/dependency policy contracts, memory delete/retention/redaction/export readiness, office context-pack architecture readiness, context snapshot public provenance projection contract, governance readiness CLI, POC runs using governed skills, and 2026-06-12 governance runtime smoke evidence. | Legacy frontend route remap/policy enforcement, signed package or SBOM review evidence, dependency vulnerability/license evidence, context-pack persistence/executor injection/frontend provenance acceptance, full dashboard/visual acceptance, and broader 211 acceptance. |
| G7 Sandbox / Resource Hardening | Blocked for high-risk expansion. | Fake provider remains local/test-only; capacity docs expose sandbox limits and missing hardening warnings. | Docker provider hardening, egress/quota policy, orphan cleanup, container security options, and Docker-capable 211 smoke. |
| G8 Multi-Agent Controlled Beta | Feature-flagged only. | Dispatcher and child-run admission work exists behind current controls. | Tenant-aware scheduling quota/backpressure, #21 capacity evidence, observability, sandbox, and tool governance gates must pass before ordinary-user exposure. |
| G9 Observability / Quality / Ops | Foundation Alpha POC release evidence exists; G9 remains partial. | Admin Runtime overview, capacity/governance/observability readiness docs and tools, error taxonomy/dashboard contracts, release-evidence contracts, trace/audit export contracts, frontend projection audit, and reviewed 211 POC smoke entry. | Runtime dashboard acceptance, recorded capacity evidence, model-gateway backpressure evidence, golden-set eval runtime, alert delivery/runtime calibration, trace/export 211 acceptance, and release-evidence runtime export/retention acceptance. |
| G10 Internal Beta / Department Rollout | Blocked. | Candidate internal workflows are named only as examples in roadmap. | Select 1-2 real internal workflow owners, complete prior gates, record cost/quality/audit/rollback evidence, and pass 211 acceptance. |

## Issue-Driven Thin Spots

| Issue area | Current judgment | Next closure action |
| --- | --- | --- |
| #17 frontend source migration | Source lives under `frontend/web` with projection audit, `ci:verify`, release traceability, GitHub Actions workflow, packaged frontend image definition, and 211 thin-shell POC smoke. | Run or refresh frontend install/lint/build when changing browser code; complete packaged frontend image smoke/release acceptance on 211 or another Docker-capable host. |
| #21 capacity baseline | Baseline plan, snapshot/verdict/profile tools, bounded probe harness, and Admin Runtime capacity/backpressure visibility exist; bounded probes now fail closed when successful Admin Runtime overview responses miss required baseline sections. | Record approved load evidence for the seven gates before raising any production default. Until then every profile remains `do_not_raise_without_recorded_load_test_evidence`. |
| G6 governance | Source-level policies and readiness contracts exist, and the Admin Runtime governance projection has a focused 211 smoke. | Convert contracts into full dashboard/visual acceptance and real reviewed Skill release evidence; keep long-term memory fail-closed. |
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
