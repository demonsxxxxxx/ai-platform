# AI Platform Technical Acceptance Matrix

> Status: active companion acceptance document.
>
> Scope: companion acceptance document for
> `docs/superpowers/specs/2026-06-10-ai-platform-product-prd-v2.md`.
> It records current module state, phased target state, open-source reference
> absorption, and first-stage acceptance standards.
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
| S0 | Current Review Baseline | Current source has substantial control-plane work and multiple readiness contracts, but several gates still lack runtime evidence. | No gate may be auto-closed from this document. |
| S1 | Foundation Alpha | Internal controlled foundation loop: source authority, control-plane contracts, safe projections, permission cards, admin visibility, release-evidence routing, and fail-closed governance are reviewable and 211-verifiable. | Production concurrency increases, high-risk Docker sandbox exposure, ordinary-user multi-agent, and long-term memory by default. |
| S2 | Governance / Operations Beta | G6/G7/G9 evidence becomes operational: skill release evidence, memory retention/delete/redaction, tool policy enforcement, Docker sandbox hardening, Admin Runtime dashboard acceptance, and recorded capacity evidence. | Department rollout and ordinary-user multi-agent expansion until selected workflows and rollback evidence exist. |
| S3 | Controlled Workflow Beta | One or two internal workflows run with explicit owners, cost/quality/audit/rollback evidence, and controlled multi-agent or long-task use where gates allow it. | Broad department rollout and unbounded platform exposure. |
| S4 | Department Rollout | Department-level usage with support model, release evidence retention, alerting, regression gates, capacity profile, and rollback procedure. | External/public productization unless separately approved. |

## 2. Open-Source Absorption Standard

External projects are implementation and product references. They do not own
ai-platform identity, tenancy, quota, audit, memory policy, artifact ACL, release
evidence, or source authority.

| Source | Absorb into modules | Absorb | Do not absorb | Acceptance impact |
| --- | --- | --- | --- | --- |
| Codex CLI | Workspace, approval policy, event sourcing, skills, shell/tool/sandbox vocabulary, bounded context. | Workspace boundary, approval modes, command/network/MCP approval vocabulary, run event stream shape, skill/plugin packaging ideas, turn/context ergonomics. | Single-user identity model, personal memory assumptions, local-only source authority, ungoverned global skill lookup. | Any Codex adapter must use platform-issued workspace, `run_tool_permission_requests`, `run_events`, and `run_skill_snapshots`. |
| Poco Claw | Claude Agent SDK runtime platformization, runtime registry, collaboration UX, run drawer/playback/artifacts. | SDK client/service separation, persistent runtime lifecycle ideas, idle/sleep/resume/keepalive concepts, execution drawer UX, private agent state separation. | Docker socket exposure as a default trust boundary, Poco tenancy as enterprise source of truth, bypassing ai-platform RBAC/quota/audit. | Runtime UX and SDK service design can be borrowed only behind ai-platform tenant, quota, event, and audit contracts. |
| DeerFlow 2.0 | Long-horizon workflows, subagent/concurrency concepts, research/report product shape. | Per-user/thread isolation techniques, guardrail middleware ideas, memory scoping patterns, long-task planning/report flow, subagent limit patterns. | A second scheduler/control plane, uncontrolled shell/MCP exposure, DeerFlow memory as enterprise memory authority. | G8/G10 may borrow concepts only after G5/G6/G7/G9 gates allow controlled long-task or multi-agent exposure. |
| LambChat | Frontend shell and existing `frontend/web` source baseline. | React/Vite shell, chat/task surface starting point, selected UI patterns after projection audit. | Model/channel/env-var management as product truth, private payload reads, raw runtime/admin backdoors. | Frontend acceptance requires public/admin projection audit and same-commit build/release traceability. |
| new-api / model gateway patterns | Model gateway routing, provider abstraction, token/cost/latency accounting. | Provider routing concepts, OpenAI-compatible surface ideas, cost/token observability. | Platform RBAC, tenant audit, memory/tool governance, secret exposure to frontend. | Model gateway work must feed Admin Runtime capacity/backpressure and cost/token/latency projections. |
| AgentScope | Concept vocabulary only unless reopened. | Agent/service/skill/workspace vocabulary when useful. | Active adapter direction, identity/RBAC/artifact/memory source of truth. | No implementation gate depends on AgentScope unless a future issue explicitly reopens it. |

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
| Current state | Trusted principal, company login path, admin role, gateway secret, and tenant/user tests exist; fresh 211 auth/session/RBAC/tenant smoke is still required. |
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
| Reference sources | Codex event/turn vocabulary, Poco runtime UX, DeerFlow long-task concepts only after platform contracts own the ledgers. |
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
| Current state | Tenant-aware queue lease, worker maintenance, active-run admission, bounded metadata, and Admin Runtime capacity/backpressure projections exist. #21 recorded load evidence remains missing. |
| S1 target | Normal controlled internal workloads run under existing defaults; capacity state is visible to admins; defaults are not raised. |
| S2 target | Seven-gate recorded load evidence exists for API burst, run creation burst, queue depth/lease latency, worker start, model gateway timeout/backpressure, sandbox/container pressure, and cleanup. |
| S3/S4 target | Capacity profiles can be operator-reviewed and selected without weakening tenant fairness or fail-closed policy. |
| Reference sources | Codex event signals for command/test/approval traces; new-api concepts for model-gateway backpressure; ai-platform owns quota/admission. |
| S1 acceptance | Queue/worker/capacity focused tests and Admin Runtime smoke or captured projection show queue/admission/backpressure/capacity status; #21 remains blocked until recorded evidence exists; no production default increases are committed without load proof. |

### 3.6 Executor Runtime And Codex / Claude Adapter Boundary

| Field | Standard |
| --- | --- |
| Current state | Claude Agent SDK runner and worker adapter exist; permission handling, pinned skill staging, and event reporting are partially implemented; Codex CLI is a reference, not yet an active platform adapter. |
| S1 target | The active executor consumes platform payloads, workspace, skill snapshots, tool policy, and event sink; it cannot bypass tenant, queue, or audit contracts. |
| S2 target | Codex-style execution events, command/test/diff summaries, approval requests, and tool results are normalized into platform ledgers. |
| S3/S4 target | Optional Codex adapter or SDK runtime variants can be introduced only through the same platform contract and evidence gates. |
| Reference sources | Codex CLI for workspace/approval/event/skills mechanics; Poco Claw for Claude Agent SDK platformization and runtime UX. |
| S1 acceptance | Executor runs use platform-issued workspace, `run_tool_permission_requests` for approval, `run_events` for replay/audit, and `run_skill_snapshots` for skill staging; executor-private logs are not source of truth. |

### 3.7 Sandbox Runtime And Resource Hardening

| Field | Standard |
| --- | --- |
| Current state | Sandbox lease, provider abstraction, callback normalization, fake provider, and some Docker-provider paths exist; default local provider remains fake; Docker hardening and 211 evidence remain open. |
| S1 target | Fake provider remains local/test-only; high-risk sandbox is not broadly exposed; lease lifecycle is platform-owned. |
| S2 target | Docker provider hardening covers egress/network policy, quota, cleanup, container security options, callback token, and Docker-capable smoke. |
| S3/S4 target | Sandbox profiles map to workflow risk classes and have operational rollback and cleanup evidence. |
| Reference sources | Codex sandbox vocabulary and approval/network model; Poco runtime lifecycle ideas. |
| S1 acceptance | Public/admin projections do not expose workdirs, raw runtime paths, command fingerprints, or private payloads; G7 remains blocked until Docker-provider smoke and hardening evidence exist. |

### 3.8 Tool Permission, MCP, And Approval Policy

| Field | Standard |
| --- | --- |
| Current state | Allow/ask/deny taxonomy, user permission card, admin policy/history routes, and permission events exist; legacy frontend route remap/enforcement and runtime acceptance remain partial. |
| S1 target | Ordinary users see safe permission cards; write-capable or high-risk tool calls require exact permission decisions; admin can inspect policy/history. |
| S2 target | Shell, network, MCP, and filesystem approvals share the same durable request/decision policy model. |
| S3/S4 target | Workflow-specific tool policies can be reviewed and audited per tenant/workspace without raw private payload exposure. |
| Reference sources | Codex CLI approval policy and command/network/MCP approval vocabulary. |
| S1 acceptance | `tests/test_tool_permission_routes.py`, `tests/test_admin_tool_policies.py`, or equivalent focused tests prove decisions bind to exact `tool_call_id` or stable request fingerprint, `allow_once` cannot be replayed across unrelated calls, and frontend consumes public/admin projections only. |

### 3.9 Skill Governance And Release Policy

| Field | Standard |
| --- | --- |
| Current state | `skill_versions`, `run_skill_snapshots`, pinned manifests, release decisions, pin mismatch checks, used-skill recording, and readiness contracts exist; SBOM/signature/license/vulnerability evidence remains partial. |
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
| Reference sources | Codex bounded context and turn mechanics; DeerFlow memory scoping ideas; ai-platform owns tenant memory policy. |
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
| Reference sources | Codex event sourcing, Poco run playback/artifact UX, DeerFlow report/artifact lineage concepts. |
| S1 acceptance | New event/artifact types include public/admin projection tests; artifact previews stay allowlisted; raw storage keys and runtime paths are never public. |

### 3.13 Model Gateway And Provider Runtime

| Field | Standard |
| --- | --- |
| Current state | OpenAI-compatible/new-api style provider routing concepts are referenced; settings expose gateway/provider fields; capacity and observability tooling report model-gateway limit/backpressure gaps; enforcement and load evidence remain incomplete. |
| S1 target | Model provider configuration is redacted from public/admin projections, token/cost/latency/error fields are platform-owned, and capacity gaps are visible without raising defaults. |
| S2 target | Provider routing, request concurrency/rate policy, timeout/backpressure, error taxonomy, cost/token accounting, and recorded model-gateway load evidence are operational. |
| S3/S4 target | Department workflows have provider policy, cost budget, alerting, fallback, and rollback evidence. |
| Reference sources | new-api/model gateway patterns for provider routing; Codex telemetry concepts for event/cost traces; ai-platform owns policy and evidence. |
| S1 acceptance | `tests/test_capacity_baseline.py`, `tests/test_capacity_bounded_load_harness.py`, observability readiness tools, or equivalent focused tests prove model-gateway secrets are redacted, `capacity.limits.model_gateway` and `backpressure.model_gateway` are visible to admins, and production defaults are not raised without recorded evidence. |

### 3.14 Admin Runtime, Observability, Quality, And Release Evidence

| Field | Standard |
| --- | --- |
| Current state | Admin Runtime overview, capacity/governance/observability readiness tools, error taxonomy contracts, release-evidence contracts, trace/audit export contracts, and frontend projection audit exist; runtime acceptance remains partial. |
| S1 target | Admin can see queue/run/sandbox/capacity/backpressure/governance gaps well enough for controlled operation. |
| S2 target | Dashboard acceptance, per-surface latency, model-gateway backpressure evidence, golden-set eval runtime, alert delivery/calibration, trace/export, and release evidence runtime export are accepted. |
| S3/S4 target | Department rollout has SLOs, alert channels, quality gates, release evidence retention, and rollback procedure. |
| Reference sources | new-api model gateway concepts; Codex event telemetry concepts; ai-platform owns observability taxonomy and release evidence. |
| S1 acceptance | Admin Runtime exposes redacted status with no secrets, raw queue payloads, sandbox workdirs, or executor-private payloads; G9 remains partial until runtime acceptance is recorded. |

### 3.15 Multi-Agent And Long-Task Runtime

| Field | Standard |
| --- | --- |
| Current state | Dispatcher, child admission, handoff, reconciliation, rollup, cancel propagation, and worker dispatcher work exists behind controls; ordinary-user exposure remains blocked. |
| S1 target | Multi-agent stays feature-flagged/internal only; no ordinary-user beta. |
| S2 target | Parent/child run ledger, quota/backpressure, cancellation, provenance, and observability pass controlled internal evidence. |
| S3 target | One or two owner-approved long-task workflows can use controlled multi-agent or subagent patterns. |
| S4 target | Department rollout uses capacity profiles, quality gates, audit, cost, and rollback evidence. |
| Reference sources | DeerFlow 2.0 long-horizon/subagent patterns; Codex CLI internal subagents as concept only; Poco collaboration UX. |
| S1 acceptance | No ordinary-user multi-agent exposure before G5/G6/G7/G9 closure; child fanout must go through active-run admission, tenant quota, backpressure, event projection, and parent cancel semantics. |

## 4. First-Stage Acceptance Checklist

Foundation Alpha is accepted only when the following S1 checks are proven with
the listed evidence. If a required S1 check is missing, the verdict is
`S1 blocked / not accepted`. Later-gate blockers must be recorded as blocked,
not counted as accepted S1 evidence.

| Check | Acceptance standard |
| --- | --- |
| Source authority | PRD v2, tech acceptance matrix, roadmap, guardrails, gate status, and source-authority tests agree on responsibilities. |
| Security baseline | Fresh 211 evidence covers auth/session/RBAC/tenant isolation/redaction. Missing evidence yields `S1 blocked / not accepted`, with blockers recorded separately in gate status. |
| Control plane contracts | Related contract/repository/route tests pass for touched areas; executor does not define platform schema. |
| DB/storage/deploy | Schema, DB pool, storage namespace, file/artifact access, and deploy config checks are redacted, tenant-scoped, and source-authority aligned. |
| Capacity | Admin Runtime shows current limits and missing evidence; production defaults are not raised without #21 recorded load evidence. |
| Model gateway | Admin projections expose model-gateway limit/backpressure gaps without secrets, and no default increase is made without recorded evidence. |
| Tool approval | Write/high-risk tool calls are blocked without exact decisions; permission cards and admin policy use public/admin projections. |
| Skills | Runs stage pinned skill snapshots and reject content hash mismatches; production release remains blocked without reviewed evidence. |
| Memory/context | Long-term cross-session memory remains fail-closed; retention/delete/redaction/opt-out are tested in current scope. |
| Frontend | Active browser graph uses public/admin projections; install/lint/type/build are reproducible or exact blockers are recorded. |
| Sandbox | Fake provider remains local/test; Docker provider hardening and 211 smoke remain G7 blockers. |
| Observability | Admin Runtime gives controlled-operation visibility; G9 remains partial until dashboard/runtime/alert/export evidence is accepted. |
| Multi-agent | Feature-flagged/internal only; no ordinary-user exposure before prior gates pass. |

## 5. Review And Update Rules

- Keep this document stable enough for acceptance review. Do not append per-PR
  command logs or image tags here.
- Put dynamic status in `docs/operations/ai-platform-gate-status.md`.
- Route per-gate or per-commit evidence to `docs/release-evidence/` or another
  approved release-evidence location as part of S1 source-authority cleanup;
  do not keep appending it to PRD or roadmap prose.
- Update this document when a module's target phase, reference-source boundary,
  or acceptance criterion changes.
- Before marking S1 accepted, run source-authority docs tests and the focused
  tests for any module whose acceptance statement changed.
