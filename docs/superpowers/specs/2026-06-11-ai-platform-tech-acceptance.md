# AI Platform Technical Acceptance Matrix

> Status: active companion acceptance document.
>
> Scope: companion acceptance document for
> `docs/superpowers/specs/2026-06-10-ai-platform-product-prd-v2.md`.
> It records current module state, phased target state, open-source reference
> absorption, and first-stage acceptance standards.
> Backend B0-B6 sequencing and backend gate boundaries are expanded in
> `docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md`.
> Frontend UI absorption boundaries are expanded in
> `docs/superpowers/specs/2026-06-18-librechat-frontend-ui-absorption-prd.md`.
>
> This document is not gate-closure evidence. Gate closure still requires the
> issue -> PR -> review -> merge -> 211 deploy/smoke -> close issue workflow
> where that workflow is required by the gate.

## 1. Phase Model

The platform should use these phase labels when discussing module progress. Gate
closure remains G0-G10; phases are planning bundles, not replacements for gates.

This matrix follows the same source boundary as the active PRD and guardrails:
local source is the current `ai-platform` repository root; 211 backend source is
`/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform`; 211 deploy
composition target is
`/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform`;
211 frontend entry is `http://10.56.0.211:18001/`; and the target backend and
worker containers are `ai-platform-api` and `ai-platform-worker`.

| Phase | Name | Target meaning | What remains blocked |
| --- | --- | --- | --- |
| S0 | Historical Review Baseline | Superseded review state before S1 acceptance. Use only for historical comparison. | No gate may be auto-closed from historical S0 wording. |
| S1 | Foundation Alpha | Historical controlled internal foundation loop accepted for the `380de6b` runtime subject: source authority, control-plane contracts, safe projections, permission cards, admin visibility, release-evidence routing, fail-closed governance, and 211 POC evidence are accepted for that baseline. | Current-source S1 completion when readiness reports `runtime_rollout_required`, production concurrency increases, high-risk Docker sandbox exposure, platform-level multi-run orchestration, department rollout, and long-term memory by default. |
| S2 | Governance / Operations Beta | First run S2-0 latest-main evidence refresh when readiness requires it, then turn the accepted S1 baseline into operational governance: signed Skill/SBOM evidence, dependency/license/vulnerability review, memory retention/delete/redaction operations, tool policy enforcement, Docker sandbox hardening in the SDK skill path, Admin Runtime dashboard acceptance, and recorded capacity-upgrade evidence. | Department rollout and platform-level multi-run orchestration until selected workflows and rollback evidence exist. |
| S3 | Controlled Workflow Beta | One or two internal workflows run with explicit owners, cost/quality/audit/rollback evidence, and advanced Claude Agent SDK task patterns where gates allow it. | Broad department rollout and unbounded platform exposure. |
| S4 | Department Rollout | Department-level usage with support model, release evidence retention, alerting, regression gates, capacity profile, and rollback procedure. | External/public productization unless separately approved. |

### 1.1 S1 Accepted Baseline

S1 / Foundation Alpha is accepted as a historical controlled baseline for the
`380de6b` runtime subject. The compact closure record is
`docs/operations/ai-platform-foundation-alpha-closure.md`, and the
operator-facing summary remains:

```powershell
python tools\foundation_alpha_readiness.py --format json
```

This accepted baseline covers the internal POC loop, source/runtime relation,
211 runtime POC smoke, Auth/RBAC/tenant/redaction evidence, platform-owned
contracts and projections, governed skill runs, exact permission and artifact
isolation controls, memory/context fail-closed controls, Admin Runtime
visibility, and Foundation Runtime concurrency correctness for the accepted
runtime subject. For the latest source, this document defers to readiness
output: if `foundation_alpha_stage_complete=false`,
`foundation_alpha_stage_status=runtime_rollout_required`, or
`foundation_runtime_concurrency_evidence` appears in stage blockers, the next
step is S2-0 latest-main runtime/concurrency/readiness refresh. The accepted
baseline does not expand production capacity, platform-level multi-run
orchestration, Docker sandbox hardening, department rollout, long-term cross-session memory,
packaged frontend release, or signed Skill/SBOM/license/vulnerability closure.

## 2. Open-Source Absorption Standard

External projects are implementation and product references. They do not own
ai-platform identity, tenancy, quota, audit, memory policy, artifact ACL, release
evidence, or source authority.

| Source | Absorb into modules | Absorb | Do not absorb | Acceptance impact |
| --- | --- | --- | --- | --- |
| Codex CLI | Workspace, approval policy, event sourcing, skills, shell/tool/sandbox vocabulary, bounded context. | Workspace boundary, approval modes, command/network/MCP approval vocabulary, run event stream shape, skill/plugin packaging ideas, turn/context ergonomics. | Single-user identity model, personal memory assumptions, local-only source authority, ungoverned global skill lookup. | Any Codex adapter must use platform-issued workspace, `run_tool_permission_requests`, `run_events`, and `run_skill_snapshots`. |
| Poco Claw | Claude Agent SDK runtime platformization, runtime registry, collaboration UX, run drawer/playback/artifacts. | SDK client/service separation, persistent runtime lifecycle ideas, idle/sleep/resume/keepalive concepts, execution drawer UX, private agent state separation. | Docker socket exposure as a default trust boundary, Poco tenancy as enterprise source of truth, bypassing ai-platform RBAC/quota/audit. | Runtime UX and SDK service design can be borrowed only behind ai-platform tenant, quota, event, and audit contracts. |
| LambChat | Frontend shell and existing `frontend/web` source baseline. | React/Vite shell, chat/task surface starting point, selected UI patterns after projection audit. | Model/channel/env-var management as product truth, private payload reads, raw runtime/admin backdoors. | Frontend acceptance requires public/admin projection audit and same-commit build/release traceability. |

Historical references not listed above are intentionally omitted from the active
absorption matrix. Reopening any omitted project as an implementation reference
requires a focused issue, gate, and source-authority review.

Backend-specific reference projects listed in the backend phased PRD may inform
memory, sandbox, capacity, model gateway, authorization, policy, observability,
and Skills management implementation. They are not added to this global matrix
unless they become active cross-module sources. Any code absorption still
requires source pinning, license/provenance review, targeted tests, explicit
gate wording, and runtime evidence when applicable.

## 2.1 Backend Productization Acceptance Bundles

The backend phased PRD decomposes S2/S3 backend work into B0-B6 bundles. These
bundles do not replace G0-G10 gates; they provide implementation order and
acceptance boundaries for backend work. The backend phased PRD is also the
source for B0-B6 evidence-level vocabulary such as `source_contract`,
`source_probe_on_target_runtime`, `controlled_live_probe`,
`live_worker_run_payload`, `live_platform_probe`, and
`operator_reviewed_recorded_snapshot`; this matrix summarizes those contracts
but does not replace them.

Backend P0 productization capabilities are the first execution focus inside the
B0-B6 model:

| Capability | Acceptance summary |
| --- | --- |
| P0-1 memory/context usable | Selected workflows use governed session/workspace context with provenance, retention, opt-out, delete/redaction, export boundaries, and deny paths. |
| P0-2 real sandbox usable | A Docker/equivalent provider runs governed SDK Skill tasks through platform lease, callback, quota, egress, artifact, cleanup, and projection controls; `fake` stays local/test-only. |
| P0-3 worker/model-gateway capacity | The 10-session x peak-4-SDK-subagent profile is measured through recorded queue, worker, model-gateway, sandbox, token/cost, event/artifact, cleanup, and rollback evidence before defaults rise. |
| P0-4 Skills management | Skills are uploaded/imported, immutable-versioned, reviewed, released, rolled back, dependency-reviewed, pinned into run snapshots, and audited before broad exposure. |

P0 capability summaries are planning labels, not gate-closure evidence. Code
absorption from backend reference projects still requires source pinning,
license/provenance review, targeted tests, explicit gate wording, and runtime
evidence when applicable.

| Backend bundle | Acceptance focus | Cannot claim without |
| --- | --- | --- |
| B0 latest-main backend readiness refresh | Current source, 211 source, deploy composition, runtime labels, backend/worker containers, and readiness output agree. | Fresh current-subject readiness and 211 source/runtime evidence. |
| B1 memory/context usable | Memory scopes, opt-out, retention, delete/redaction, export, context packs, provenance, and deny paths. | Workflow-level 211 smoke and long-term memory policy closure for the enabled scope. |
| B2 real sandbox usable | Docker/equivalent provider, lease lifecycle, quota, egress, callback validation, cleanup, orphan scan, and projection safety. | Docker-capable 211 smoke and hardening evidence; `fake` is never production proof. |
| B3 worker/model-gateway capacity | Tenant-aware admission, queue bounds, worker heartbeat, model-gateway pressure, token/cost ledger, and SDK subagent fanout load. | Recorded seven-gate capacity evidence and operator approval before raising defaults. |
| B4 Skills management/release governance | Upload, immutable versioning, release/rollback, dependency review, pinned run snapshots, visibility, and audit. | Reviewed SBOM/license/vulnerability/dependency evidence and a 211 governed Skill smoke. |
| B5 files/artifacts/tool permission governance | File namespaces, artifact ACL, preview/download authorization, exact tool decisions, and replay denial. | Runtime smoke proving upload -> run -> artifact access and unauthorized denial. |
| B6 Operations beta readiness | Admin Runtime, trace/export, alerts, golden-set eval, release evidence export, workflow owners, and rollback drills. | Named workflow owners, capacity profile, quality/cost/audit evidence, and 211 acceptance. |

Status labels must stay separate: `local partial`, `PR ready`, `reviewed`,
`merged`, `211 verified`, and `gate closable` are not interchangeable.

B3 operator-reviewed recorded snapshot source contract:
`ai-platform.capacity-operator-reviewed-recorded-snapshot-contract.v1` records
the `b3_10x4_sdk_subagents` profile, the required seven load-test gates, the
required profile evidence, and the operator review evidence keys before runtime
load testing. It is source contract only; it does not raise production defaults
or claim safe concurrency, does not enable ordinary-user platform-level
multi-run orchestration exposure, and does not close B3 or G9. It also does not
reopen G8; G8 stays deferred unless a later focused gate explicitly reopens
platform-level orchestration.
B3 evidence must use `b3_10x4_sdk_subagents` and
`ordinary_user_platform_multi_run_orchestration_enabled=false`; do not report
`g8_ordinary_user_multi_agent_exposure` as a B3 blocker or closure field.

## 3. Module Acceptance Matrix

Each module entry lists current status, staged target state, reference sources,
and acceptance criteria. "Current" means current repository review state, not a
fresh 211 gate closure.

### 3.1 Source Authority, Docs, And Release Evidence

| Field | Standard |
| --- | --- |
| Current state | PRD v2 is the active product source; old PRD remains a migration appendix for detailed contract checks; roadmap has G0-G10 sync but still contains legacy execution evidence; gate status snapshot exists. |
| S1 target | PRD v2, roadmap, guardrails, gate status, release-evidence routing, and source-authority tests agree on document responsibilities and G0-G10 language. |
| S2 target | Release evidence runtime export, retention, dashboard, and operator review flows are accepted. |
| S3/S4 target | Gate closure reports can trace issue, PR, review, tests, deploy, smoke, and rollback evidence without reading historical chat. |
| Reference sources | ai-platform-owned workflow; no external source should define authority. |
| S1 acceptance | User/product-owner approves authority flip; old PRD is archived or redirected; dynamic per-commit logs are routed out of product requirements; `tests/test_source_authority_docs.py` passes; docs do not embed real secrets or personal machine paths. |

S1 operator summary: run
`python tools/foundation_alpha_readiness.py --format json`. The readiness output
may mark `runtime_relevant_source_verified_by_running_runtime=true`, but S1
remains constrained until its `open_followups` are explicitly accepted or moved
to later gates. If `current_source_exact_runtime_commit_match=false`, operators
must read `runtime_source_relation`; only
`runtime_current_for_runtime_relevant_source` with empty
`runtime_affecting_dirty_paths` and empty
`runtime_affecting_changes_since_runtime_subject` allows the controlled core
POC loop to remain runtime-relevant without another runtime rollout.
`current_source_verified_by_running_runtime` and
`controlled_poc_loop_verified_for_current_source` stay false until the running
image matches the exact current source tree.

### 3.2 Auth, RBAC, Tenant Isolation

| Field | Standard |
| --- | --- |
| Current state | Trusted principal, company login path, admin role, gateway secret, tenant/user tests, and accepted `380de6b` 211 Auth/RBAC/tenant/redaction smoke exist; fresh 211 smoke is required for current-source claims when runtime-affecting changes have not been rolled out. |
| S1 target | Company auth/session, admin/ordinary role checks, tenant/workspace/user isolation, and redaction are basic-operational on 211. |
| S2 target | Cross-tenant and cross-role regression evidence is part of PR/deploy gates. |
| S3/S4 target | Department workflows have explicit owner, tenant, workspace, role, audit, support, and rollback model. |
| Reference sources | ai-platform only; external projects cannot define enterprise identity. |
| S1 acceptance | 211 smoke proves ordinary and admin access paths, same-tenant access, cross-tenant denial, redaction, and no fallback to stale source authority. |

### 3.3 Control Plane Contracts

| Field | Standard |
| --- | --- |
| Current state | Session, run, file, artifact, skill, tool, memory, event, audit, context snapshot, sandbox lease, and queue contracts have substantial code and tests. |
| S1 target | Frontend, worker, and executor changes are reviewed against platform-owned contracts; executor does not define platform schema. |
| S2 target | Contract drift checks and full regression are standard before PR/deploy in shared contract areas. |
| S3/S4 target | Workflow-specific contracts are additive and do not bypass core platform ledgers. |
| Reference sources | Codex event/turn vocabulary and Poco runtime UX; ai-platform owns platform ledgers. |
| S1 acceptance | Contract/repository/route tests cover happy path and at least one denial/error path for touched shared contracts; public/admin projections do not expose private payloads. |

### 3.4 Database, Storage, Schema, And Deploy Config

| Field | Standard |
| --- | --- |
| Current state | `app/db.py`, `app/schema.sql`, `app/storage.py`, file/artifact routes, redacted deploy templates, and source-authority tests exist; DB/storage/deploy runtime parity evidence still depends on 211 checks. |
| S1 target | Database pool, schema ownership, object storage namespace, file/artifact access, and deploy config templates are documented, redacted, tenant-scoped, and source-authority aligned. |
| S2 target | Migration/drift checks, storage lifecycle, release-evidence retention, and deploy/runtime config parity are part of PR/deploy acceptance. |
| S3/S4 target | Department workflows have storage retention, backup/restore, deploy rollback, and evidence retention policy. |
| Reference sources | ai-platform only for schema and deploy authority; external projects may not define DB schema, object-storage keys, or `.env` shape. |
| S1 acceptance | `tests/test_schema.py`, artifact/file permission tests, source-authority docs tests, and a redacted 211 deploy-config/source-label check cover the touched paths; no real `.env`, DSN password, object-storage secret, raw storage key, or personal path is committed or exposed. |

### 3.5 Queue, Worker, Capacity, And Backpressure

| Field | Standard |
| --- | --- |
| Current state | Tenant-aware queue lease, worker maintenance, active-run admission, bounded metadata, Admin Runtime capacity/backpressure projections, and accepted `380de6b` Foundation Runtime concurrency evidence exist. GitHub #21 is closed, but the capacity-upgrade evidence gate still lacks recorded seven-gate load evidence. |
| S1 target | Normal controlled internal workloads run under existing defaults; capacity state is visible to admins; defaults are not raised. |
| S2 target | Seven-gate recorded load evidence exists for API burst, run creation burst, queue depth/lease latency, worker start, model gateway timeout/backpressure, sandbox/container pressure, and cleanup; the B3 `b3_10x4_sdk_subagents` profile has operator-reviewed evidence for 10 sessions x peak 4 SDK subagents/session. |
| S3/S4 target | Capacity profiles can be operator-reviewed and selected without weakening tenant fairness or fail-closed policy. |
| Reference sources | Codex event signals for command/test/approval traces; ai-platform-owned model-gateway backpressure requirements; ai-platform owns quota/admission. |
| S1 acceptance | Queue/worker/capacity focused tests and Admin Runtime smoke or captured projection show queue/admission/backpressure/capacity status; the capacity-upgrade evidence gate remains blocked until recorded evidence exists; no production default increases are committed without load proof. |

### 3.6 Executor Runtime And Codex / Claude Adapter Boundary

| Field | Standard |
| --- | --- |
| Current state | Claude Agent SDK runner and worker adapter are the active execution route; permission handling, pinned skill staging, artifact/event reporting, and SDK tool allowlisting exist and must remain platform-governed; Codex CLI is a reference, not yet an active platform adapter. |
| S1 target | The active executor consumes platform payloads, workspace, skill snapshots, tool policy, and event sink; it cannot bypass tenant, queue, or audit contracts. |
| S2 target | Codex-style execution events, command/test/diff summaries, approval requests, tool results, SDK Agent/subagent tool events, artifact collection, and token/cost usage are normalized into platform ledgers; capacity evidence covers the case where many sessions invoke SDK subagents concurrently. |
| S3/S4 target | Optional Codex adapter or SDK runtime variants can be introduced only through the same platform contract and evidence gates. |
| Reference sources | Codex CLI for workspace/approval/event/skills mechanics; Poco Claw for Claude Agent SDK platformization and runtime UX. |
| S1 acceptance | Executor runs use platform-issued workspace, `run_tool_permission_requests` for approval, `run_events` for replay/audit, and `run_skill_snapshots` for skill staging; executor-private logs are not source of truth. |

### 3.7 Sandbox Runtime And Resource Hardening

| Field | Standard |
| --- | --- |
| Current state | Sandbox lease, provider abstraction, callback normalization, fake provider, Docker-provider hardening evidence for reviewed prior runtime subjects, reviewed explicit verifier-path hardening evidence for `9c669761`, reviewed 2026-07-03 live-default G7/FRC evidence for `9c669761`, and reviewed 2026-07-03 label-clean live-default G7/FRC evidence for `15903fd` exist; default local provider remains fake. The `9c669761` same-subject evidence pair can support `candidate_evidence_requires_review`, but its status-review artifact records `status_upgrade_decision=not_approved_for_closure`. The newer `15903fd` label-clean evidence pair is current runtime evidence and its status-review artifact records `status=candidate_evidence_requires_review`, `g7_runtime_blocking_reasons=[]`, and `status_upgrade_decision=not_approved_for_closure`; B3 recorded load/profile evidence remains a separate blocker. |
| S1 target | Fake provider remains local/test-only; high-risk sandbox is not broadly exposed; lease lifecycle is platform-owned. |
| S2 target | Docker provider hardening covers egress/network policy, quota, cleanup, container security options, callback token, and Docker-capable smoke. |
| S3/S4 target | Sandbox profiles map to workflow risk classes and have operational rollback and cleanup evidence. |
| Reference sources | Codex sandbox vocabulary and approval/network model; Poco runtime lifecycle ideas. |
| S1 acceptance | Public/admin projections do not expose workdirs, raw runtime paths, command fingerprints, or private payloads; Docker-provider smoke and hardening evidence can move G7 to `candidate_evidence_requires_review`, but G7 is not closed until operator status-upgrade review confirms the source/runtime boundary and non-expansion invariants. |

### 3.8 Tool Permission, MCP, And Approval Policy

| Field | Standard |
| --- | --- |
| Current state | Allow/ask/deny taxonomy, user permission card, admin policy/history routes, permission events, and admin bulk-review source-route runtime-control tests exist; legacy frontend route remap/enforcement, visual acceptance, and 211 acceptance remain partial. |
| S1 target | Ordinary users see safe permission cards; write-capable or high-risk tool calls require exact permission decisions; admin can inspect policy/history. |
| S2 target | Shell, network, MCP, and filesystem approvals share the same durable request/decision policy model. |
| S3/S4 target | Workflow-specific tool policies can be reviewed and audited per tenant/workspace without raw private payload exposure. |
| Reference sources | Codex CLI approval policy and command/network/MCP approval vocabulary. |
| S1 acceptance | `tests/test_tool_permission_routes.py`, `tests/test_admin_tool_policies.py`, or equivalent focused tests prove decisions bind to exact `tool_call_id` or stable request fingerprint, `allow_once` cannot be replayed across unrelated calls, and frontend consumes public/admin projections only. |

### 3.9 Skill Governance And Release Policy

| Field | Standard |
| --- | --- |
| Current state | `skill_versions`, `run_skill_snapshots`, pinned manifests, release decisions, pin mismatch checks, used-skill recording, readiness contracts, and Admin Skill release dashboard source-route runtime-control tests exist; SBOM/signature/license/vulnerability evidence, visual acceptance, and 211 acceptance remain partial. |
| S1 target | Runs use published or governed skill versions and pinned snapshots; global mutable skill lookup is not a production behavior. |
| S2 target | Skill release dashboard and manifests include signed package or SBOM evidence, license review, vulnerability review, dependency policy, and Admin acceptance. |
| S3/S4 target | Department workflows use reviewed skill packages with release evidence and rollback path. |
| Reference sources | Codex skill/plugin packaging ideas; Poco/Claude SDK staging patterns where compatible with platform pinning. |
| S1 acceptance | `tests/test_skill_pinning.py`, `tests/test_contract.py`, `tests/test_claude_agent_worker_adapter.py`, or equivalent focused tests prove new runs stage pinned snapshots, reject content hash mismatches, record used skills, and keep production skill release blocked without reviewed evidence. |

### 3.10 Memory And Context

| Field | Standard |
| --- | --- |
| Current state | Session-bound memory, opt-out, retention cleanup, redaction, admin inventory, and delete/export readiness contracts exist; document-centric context-pack runtime persistence/injection remains incomplete. |
| S1 target | Long-term cross-session memory remains fail-closed; current memory/context behavior is tenant/session scoped and redacted. |
| S2 target | Retention/delete/redaction/export evidence and frontend provenance are accepted; document task context packs can be persisted and injected safely. |
| S3/S4 target | Workflow memory policies can be enabled per tenant/workspace/user with opt-out and audit evidence. |
| Reference sources | Codex bounded context and turn mechanics; ai-platform owns tenant memory policy. |
| S1 acceptance | `tests/test_context_routes.py`, `tests/test_repositories.py`, memory readiness tools, or equivalent focused tests prove tenant/workspace/user/session scope, opt-out, retention, delete/redaction behavior, and no executor-private payload or raw memory content in public projections. |

### 3.11 Frontend Web And Public/Admin Projections

| Field | Standard |
| --- | --- |
| Current state | `frontend/web` is imported; projection audit, lint/type/build scripts, release traceability, CI direction, and packaged image definition exist; policy gaps remain for inactive legacy sources and packaged smoke. |
| S1 target | Frontend can be installed/linted/built or has a precise blocker; active browser graph uses ai-platform public/admin projections only. |
| S2 target | Packaged frontend image smoke and release traceability pass on a Docker-capable host. |
| S3/S4 target | Department workflow UI has run drawer, playback, artifacts, permission cards, context provenance, and admin runtime visibility. |
| Reference sources | LambChat as source baseline; Poco Claw for run drawer/playback/artifact UX; Codex for approval-event ergonomics. |
| S1 acceptance | Projection audit reports no active private-payload violations; legacy model/channel/env-var surfaces remain hidden/remapped; browser code does not read raw storage keys, sandbox workdirs, secrets, or executor private payloads. |

### 3.12 Artifacts, Event Playback, And Provenance

| Field | Standard |
| --- | --- |
| Current state | Artifact card, preview allowlist, run playback, SSE/event projections, visible-to-user controls, and redaction tests exist; new event/artifact types can still create drift if not tested. |
| S1 target | Ordinary users can inspect safe run progress, permission cards, and authorized artifacts without private payload exposure. |
| S2 target | Provenance, artifact tree, trace/export, and release evidence are operationally accepted. |
| S3/S4 target | Workflow artifacts have lineage, reuse policy, versioning, retention, and rollback evidence. |
| Reference sources | Codex event sourcing and Poco run playback/artifact UX; ai-platform owns artifact lineage and release evidence. |
| S1 acceptance | New event/artifact types include public/admin projection tests; artifact previews stay allowlisted; raw storage keys and runtime paths are never public. |

### 3.13 Model Gateway And Provider Runtime

| Field | Standard |
| --- | --- |
| Current state | OpenAI-compatible provider-routing concepts are referenced; settings expose gateway/provider fields; capacity and observability tooling report model-gateway limit/backpressure gaps; enforcement and load evidence remain incomplete. |
| S1 target | Model provider configuration is redacted from public/admin projections, token/cost/latency/error fields are platform-owned, and capacity gaps are visible without raising defaults. |
| S2 target | Provider routing, request concurrency/rate policy, timeout/backpressure, error taxonomy, cost/token accounting, and recorded model-gateway load evidence are operational. |
| S3/S4 target | Department workflows have provider policy, cost budget, alerting, fallback, and rollback evidence. |
| Reference sources | OpenAI-compatible provider-routing concepts and Codex telemetry concepts for event/cost traces; ai-platform owns policy and evidence. |
| S1 acceptance | `tests/test_capacity_baseline.py`, `tests/test_capacity_bounded_load_harness.py`, observability readiness tools, or equivalent focused tests prove model-gateway secrets are redacted, `capacity.limits.model_gateway` and `backpressure.model_gateway` are visible to admins, and production defaults are not raised without recorded evidence. |

### 3.14 Admin Runtime, Observability, Quality, And Release Evidence

| Field | Standard |
| --- | --- |
| Current state | Admin Runtime overview, capacity/governance/observability readiness tools, error taxonomy contracts, release-evidence contracts, trace/audit export contracts, and frontend projection audit exist; runtime acceptance remains partial. |
| S1 target | Admin can see queue/run/sandbox/capacity/backpressure/governance gaps well enough for controlled operation. |
| S2 target | Dashboard acceptance, per-surface latency, model-gateway backpressure evidence, golden-set eval runtime, alert delivery/calibration, trace/export, and release evidence runtime export are accepted. |
| S3/S4 target | Department rollout has SLOs, alert channels, quality gates, release evidence retention, and rollback procedure. |
| Reference sources | OpenAI-compatible model gateway concepts and Codex event telemetry concepts; ai-platform owns observability taxonomy and release evidence. |
| S1 acceptance | Admin Runtime exposes redacted status with no secrets, raw queue payloads, sandbox workdirs, or executor-private payloads; G9 remains partial until runtime acceptance is recorded. |

### 3.15 Advanced Claude Agent SDK Task Patterns

| Field | Standard |
| --- | --- |
| Current state | Claude Agent SDK runner and worker adapter are the execution path. SDK Agent/subagent capability is treated as available execution-layer capability inside one governed platform run. Historical platform dispatcher/parent-child work exists behind controls but is not the current product route. |
| S1 target | SDK-internal agent/subagent behavior is governed as one platform run; no ordinary-user platform-level multi-run orchestration. |
| S2 target | Claude Agent SDK agent/subagent execution is governed through platform payloads, tool allowlists, permission policy, artifact/event/token ledgers, sandbox policy, and cost accounting; recorded load evidence proves the target runtime can sustain expected per-session SDK subagent fanout before broad exposure. |
| S3 target | One or two owner-approved long-task workflows can use controlled SDK task patterns without introducing platform-owned parent/child orchestration unless a later gate reopens it; each workflow declares expected subagent fanout, timeout, model-gateway pressure, cost budget, and rollback path. |
| S4 target | Department rollout uses capacity profiles, quality gates, audit, cost, and rollback evidence. |
| Reference sources | Claude Agent SDK Agent/subagent capability for execution; Codex CLI internal subagents as concept only; Poco collaboration UX. |
| S1 acceptance | SDK-private subagents are not automatically platform-visible multi-run or parent/child orchestration; platform-visible parent/child orchestration is deferred and must not appear in ordinary-user contracts. This acceptance does not prove that every user session can safely invoke SDK subagents at production concurrency. |

## 4. First-Stage Acceptance Checklist

Foundation Alpha is accepted for the `380de6b` controlled internal baseline.
The following checks describe the accepted S1 regression envelope: future
source, runtime, or evidence changes must preserve these properties or
explicitly downgrade the current-source state back to `local partial` /
`211 verification required` / `runtime_rollout_required`. Later-gate blockers
remain blocked after S1 baseline acceptance and must not be counted as
production or beta evidence.

| Check | Acceptance standard |
| --- | --- |
| Source authority | PRD v2, tech acceptance matrix, roadmap, guardrails, gate status, and source-authority tests agree on responsibilities. |
| Security baseline | Accepted `380de6b` 211 evidence covers auth/session/RBAC/tenant isolation/redaction; latest current-source claims require fresh evidence when readiness reports runtime rollout required. |
| Control plane contracts | Related contract/repository/route tests pass for touched areas; executor does not define platform schema. |
| DB/storage/deploy | Schema, DB pool, storage namespace, file/artifact access, and deploy config checks are redacted, tenant-scoped, and source-authority aligned. |
| Capacity | Admin Runtime shows current limits and missing evidence; production defaults are not raised without recorded capacity-upgrade evidence. |
| Model gateway | Admin projections expose model-gateway limit/backpressure gaps without secrets, and no default increase is made without recorded evidence. |
| Tool approval | Write/high-risk tool calls are blocked without exact decisions; permission cards and admin policy use public/admin projections. |
| Skills | Runs stage pinned skill snapshots and reject content hash mismatches; production release remains blocked without reviewed evidence. |
| Memory/context | Long-term cross-session memory remains fail-closed; retention/delete/redaction/opt-out are tested in current scope. |
| Frontend | Active browser graph uses public/admin projections; install/lint/type/build are reproducible or exact blockers are recorded. |
| Sandbox | Fake provider remains local/test; reviewed Docker provider hardening and 211 smoke can support `candidate_evidence_requires_review`, but G7 remains unclosed until operator status-upgrade review and current source/runtime boundaries are reconciled. |
| Observability | Admin Runtime gives controlled-operation visibility; G9 remains partial until dashboard/runtime/alert/export evidence is accepted. |
| Platform multi-run / SDK subagent | SDK-internal agent/subagent behavior stays inside one governed platform run; platform-level multi-run orchestration remains deferred with no ordinary-user exposure before prior gates pass. |

## 5. Review And Update Rules

- Keep this document stable enough for acceptance review. Do not append per-PR
  command logs or image tags here.
- Put dynamic status in `docs/operations/ai-platform-gate-status.md`.
- Route per-gate or per-commit evidence to `docs/release-evidence/` or another
  approved release-evidence location as part of S1 source-authority cleanup;
  do not keep appending it to PRD or roadmap prose.
- Update this document when a module's target phase, reference-source boundary,
  or acceptance criterion changes.
- Before changing S1 acceptance status or expanding beyond S1 boundaries, run
  source-authority docs tests and the focused tests for any module whose
  acceptance statement changed.
