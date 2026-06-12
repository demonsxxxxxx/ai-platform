# ai-platform Gate Status Snapshot

Date: 2026-06-12

This snapshot keeps the current PRD, foundation roadmap, guardrails, repository
code, 211 runtime, and issue-driven priorities aligned. It is not automatic
gate-closure evidence and must not be used to auto-close GitHub issues. Gate
closure still requires the
issue -> PR -> review -> merge -> 211 deploy/smoke -> close issue workflow in
`docs/agent-rules/github-issue-pr-workflow.md`.

## Foundation Alpha POC Smoke

Status: 211 POC smoke refreshed for the current context public-summary
contract; not production gate closure.

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

Current live 211 API/worker runtime and active Foundation Alpha POC
evidence subject is `d4486ebf5a33ce23a632a69bcf07ef1220b61ea3`. On
2026-06-12, source and API/worker were advanced to that commit with image
`ai-platform:d4486eb-observability-evidence-loader` and image ID
`sha256:0d3c89127d278bee7c2a64384fbd67560ec9300536571c1e6845a8b00376cdb5`.
The 211 source marker, source revision label, OCI revision label, and image
internal source marker pointed to `d4486eb`; API health returned `ok`; and
compose labels pointed to the repo-local deploy composition. The image/container
labels still carried stale `ai-platform.runtime-subject`,
`ai-platform.runtime-rollout`, and `ai-platform.source_revision` alias metadata
from prior rollouts, and compose labels still recorded the old external
env-file path, so this evidence does not close G0 source-authority parity.

The full d4486eb POC evidence refresh includes reviewed, redacted entries for
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, alert/trace export runtime acceptance, and
packaged frontend blocker evidence:
`docs/release-evidence/foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-release-evidence-runtime-acceptance.json`,
`docs/release-evidence/foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-alert-trace-export-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-frontend-packaged-runtime-smoke-blocked.json`.
This refresh verifies the controlled POC loop for the d4486eb runtime subject
but does not close Foundation Alpha.

The immediately superseded runtime subject commit
`00e4e6b950709439850749fe26af9c0943f6a07c` remains historical reviewed evidence for the skill-release
pending-evidence hardening slice. Its reviewed, redacted smoke evidence entries
are under
`docs/release-evidence/foundation-alpha-poc/00e4e6b950709439850749fe26af9c0943f6a07c/`.

The earlier superseded runtime subject commit
`6088d5d179c422a6d753e1b77079410503e58925` was synced to the 211 source
target and the 211 API and worker ran
`ai-platform:6088d5d-alert-trace-acceptance` with image ID
`sha256:c8585918ccaeb4f9128c2c9301c8f8ac0d0c40002dc5b4febcafa2813b28bedf`.
The `ai-platform.source-revision`, `ai-platform.runtime-subject`, and
`org.opencontainers.image.revision` labels all pointed to
`6088d5d179c422a6d753e1b77079410503e58925`. API health returned `ok`; the
compose config used the repo-local deploy composition while container labels
still recorded the old external env-file layout; and the
aggregate verifier `tools/verify_poc_gate.py` returned `ok: true` on 211 for
the controlled context public-projection and alert-trace-runtime-acceptance
rollout slice. That smoke verified `summary_source=chat_stream`, safe
`input_keys=["attachments","message"]` for a file-backed run, memory policy
source, execution tier, generated-at presence, and no raw material IDs or
forbidden projection leaks. The reviewed, redacted release-evidence entry is
`docs/release-evidence/foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-runtime-poc-smoke.json`.

The focused Auth/RBAC verifier `tools/verify_auth_rbac_smoke.py` also returned
`ok: true` on 211 against `6088d5d`. It verified unauthenticated auth
rejection, trusted platform principal projection, invalid gateway secret
rejection, ordinary-user Admin Runtime denial, and same-tenant Admin Runtime
access for an admin smoke principal. The reviewed, redacted Auth/RBAC evidence
entry is
`docs/release-evidence/foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-auth-rbac-smoke.json`.

The focused governance verifier
`tools/verify_governance_runtime_smoke.py` returned `ok: true` on 211 against
the same `6088d5d` runtime subject. It verified ordinary-user Admin Runtime
denial, same-tenant admin access, G6 governance schema
`ai-platform.governance-readiness.v1`, required tool/skill/memory governance
domains, tool policy taxonomy and bulk-review signals, skill release/dashboard
signals with `dashboard_contract` trimmed from the overview projection, memory
fail-closed/context-provenance/office-context signals, and no forbidden
projection terms in the reviewed summary. The reviewed, redacted governance
smoke evidence entry is
`docs/release-evidence/foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-governance-runtime-smoke.json`.

The runtime-packaged release evidence verifier
`tools/verify_release_evidence_runtime_acceptance.py` returned `ok: true`
inside the `6088d5d` API container. It verified the safe reviewed index with
`safe_entry_count=30`, `blocked_entry_count=0`, `excluded_entry_count=18`, and
the review-first retention policy. The reviewed, redacted runtime acceptance
evidence entry is
`docs/release-evidence/foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-release-evidence-runtime-acceptance.json`.

The alert/trace export runtime acceptance verifier
`tools/verify_alert_trace_export_runtime_acceptance.py` returned `ok: true`
inside the same `6088d5d` API container. It verified ordinary-user Admin
Runtime denial, same-tenant admin observability access, alert rule/template
exposure, alert delivery policy intentionally not enabled, and trace export
contract sources limited to reviewed public/admin summaries. The reviewed,
redacted runtime acceptance evidence entry is
`docs/release-evidence/foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-alert-trace-export-runtime-acceptance.json`.

This rollout used the repo-local 211 deploy composition while reusing the
existing external runtime env file without printing or copying secret values.
It used a runtime-only image rebased from the previous healthy image because
dependencies did not change. Treat that as a deployment workaround, not the
preferred release path.

The immediately superseded runtime subject commit
`948179c73734aa61ed764fb3485f5415fca8f193` remains historical reviewed
evidence for the skill-release-scaffold slice. Its reviewed, redacted smoke
evidence entries are
`docs/release-evidence/foundation-alpha-poc/948179c73734aa61ed764fb3485f5415fca8f193/2026-06-12-211-foundation-alpha-poc-948179c-skill-release-scaffold-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/948179c73734aa61ed764fb3485f5415fca8f193/2026-06-12-211-foundation-alpha-poc-948179c-auth-rbac-smoke.json`, and
`docs/release-evidence/foundation-alpha-poc/948179c73734aa61ed764fb3485f5415fca8f193/2026-06-12-211-foundation-alpha-poc-948179c-governance-runtime-smoke.json`.

The earlier superseded runtime subject commit
`b7689d0cbc6fa3913de47aea3aded1036f0ea0ae` remains historical reviewed
evidence for the context public-projection slice. Its reviewed, redacted smoke
evidence entries are
`docs/release-evidence/foundation-alpha-poc/b7689d0cbc6fa3913de47aea3aded1036f0ea0ae/2026-06-12-211-foundation-alpha-poc-b7689d0-context-public-projection-smoke.json`
and
`docs/release-evidence/foundation-alpha-poc/b7689d0cbc6fa3913de47aea3aded1036f0ea0ae/2026-06-12-211-foundation-alpha-poc-b7689d0-auth-rbac-smoke.json`.

The earlier superseded runtime subject commit
`2384e19dcac2e39fbcf9c27dc990f5774d391422` remains historical reviewed
evidence for the context source-provenance and governance slices. Its reviewed,
redacted smoke evidence entries are
`docs/release-evidence/foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-context-source-provenance-smoke.json`
and
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
`3874281276c84a418bd08bda56d7ea55b52970b7` remains retained as historical evidence only; the `d4486eb` evidence above is the active Foundation Alpha POC reference.
The immediately superseded runtime image was `ai-platform:948179c-skill-release-scaffold`.

This smoke does not close #21 capacity, G7 Docker sandbox hardening, ordinary
user multi-agent exposure, department rollout, alert delivery enablement,
signed Skill package or SBOM review evidence, G9 Admin Runtime observability
partial follow-ups, or packaged frontend image release acceptance.

On 2026-06-12, commit `83a500ef082a47db0a01b4fb9679e67bf2b24fc4` was synced to
the 211 source target for the packaged frontend image slice. The source archive
included the frontend image `ai-platform.source-revision` label contract and
passed `python3 -m compileall -q app tools scripts` on 211. The packaged
frontend runtime smoke did not reach image runtime: the Docker daemon still had
a stale registry proxy, BuildKit could not resolve the Dockerfile frontend, and
a no-syntax probe could not pull `node:22-alpine`; `nginx:1.27-alpine` also
remains required for the final image. The verifier classified the redacted
attempt as `blocked_environment` with `docker_registry_proxy_unreachable` and
`base_image_pull_failed`, with no closed evidence items. This is 211-verified
blocker evidence only; it is not packaged frontend image release acceptance.

After the active `d4486ebf5a33ce23a632a69bcf07ef1220b61ea3` runtime refresh,
the packaged frontend blocker was rechecked on 211. The source marker pointed
to `d4486eb`, the frontend Dockerfile and repo-local frontend compose overlay
were present, and the Docker daemon still had an unreachable registry proxy.
Required base-image metadata could not be pulled, and no target
`ai-platform-frontend:*` image was cached. The reviewed, redacted blocker
evidence entry is
`docs/release-evidence/foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-frontend-packaged-runtime-smoke-blocked.json`.
It records `blocked_environment` with `docker_registry_proxy_unreachable` and
`base_image_pull_failed`, has no closed evidence items, and still does not
close `packaged_frontend_image_release_acceptance`.

## Current Gate Status

| Gate | Current status | Evidence now in repository | Remaining blocker before closure |
| --- | --- | --- | --- |
| G0-G1 Source Authority / Security Baseline | Foundation Alpha POC has fresh 211 source/deploy/source-revision evidence with stale runtime-subject label follow-up, company-login audit evidence, and focused Auth/RBAC smoke evidence; keep under regression. | PRD v2, tech acceptance matrix, roadmap, guardrails, source-authority tests, repo-local compose context, frontend source migration, redacted deploy templates, and 2026-06-12 POC release evidence. | Full issue/PR/review closure path and broader auth/session/RBAC/tenant/redaction regression are still required before production closure. |
| G2-G4 Control Plane MVP | Substantial coverage; keep under regression. | Session/run/file/artifact/skill/tool/memory/event/audit contracts, repositories, routes, schema indexes, and focused tests. | Full regression before PR/deploy, plus no executor-owned platform schema drift. |
| G5 Run Lifecycle / Worker Runtime V1 | Foundation Alpha POC verified queue/run/worker execution and Admin capacity/backpressure projection; not capacity-closed. | Tenant-aware queue lease, worker maintenance, active-run admission, bounded metadata, Admin Runtime capacity/backpressure projection, #20 roadmap closure notes, and 2026-06-12 POC verifier evidence. | #21 remains open: large queue bounded lookup pressure, multi-tenant load, and recorded seven-gate load evidence are still missing. Production defaults stay unchanged. |
| G6 Tool / Skill / Memory Governance | Admin Runtime governance projection now has focused 211 smoke evidence for the POC runtime, but G6 remains partial and ordinary-user expansion remains blocked. | Tool policy taxonomy/history, public permission-card projection, skill release/dependency policy contracts, memory delete/retention/redaction/export readiness, office context-pack architecture readiness, context snapshot public provenance projection contract, governance readiness CLI, POC runs using governed skills, and 2026-06-12 governance runtime smoke evidence. | Legacy frontend route remap/policy enforcement, signed package or SBOM review evidence, dependency vulnerability/license evidence, context-pack persistence/executor injection/frontend provenance acceptance, full dashboard/visual acceptance, and broader 211 acceptance. |
| G7 Sandbox / Resource Hardening | Blocked for high-risk expansion. | Fake provider remains local/test-only; capacity docs expose sandbox limits and missing hardening warnings. | Docker provider hardening, egress/quota policy, orphan cleanup, container security options, and Docker-capable 211 smoke. |
| G8 Multi-Agent Controlled Beta | Feature-flagged only. | Dispatcher and child-run admission work exists behind current controls. | Tenant-aware scheduling quota/backpressure, #21 capacity evidence, observability, sandbox, and tool governance gates must pass before ordinary-user exposure. |
| G9 Observability / Quality / Ops | Foundation Alpha POC release evidence and alert/trace export runtime acceptance exist; G9 remains partial. | Admin Runtime overview, capacity/governance/observability readiness docs and tools, error taxonomy/dashboard contracts, release-evidence contracts, reviewed 211 release-evidence runtime export/retention acceptance for `d4486eb`, reviewed 211 alert/trace export runtime acceptance for `d4486eb`, trace/audit export contracts, frontend projection audit, and reviewed 211 POC smoke entry. | Runtime dashboard acceptance, recorded capacity evidence, model-gateway backpressure evidence, golden-set eval runtime, alert delivery enablement/runtime calibration, and remaining G9 Admin Runtime observability follow-ups. |
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
