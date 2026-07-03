# AI Platform Product PRD v2

> Status: active product PRD. S1 / Foundation Alpha historical baseline is
> accepted for the `380de6b` runtime subject. Current-source S1 status is not
> assumed from that closure; it is determined by
> `tools/foundation_alpha_readiness.py --format json`.
>
> Purpose: restate the ai-platform product goal, architecture, reference-source
> boundaries, module weaknesses, gate roadmap, accepted first-stage baseline,
> and next-stage direction.
> The 2026-05-29 PRD remains a migration appendix for detailed contract checks
> that have not yet been moved into this PRD, guardrails, or focused acceptance
> documents.

## 1. Product Goal

ai-platform is a company-internal, multi-tenant Agent platform. Its core value is
not a single-user coding assistant, a generic workflow demo, or a packaged Docker
sample. Its core value is an enterprise control plane that lets internal users
run AI tasks while the platform enforces tenant/workspace/user/session isolation,
tool permission, memory governance, sandbox risk control, observability, audit,
and release evidence.

The target platform must support:

| Capability | Target |
| --- | --- |
| Multi-tenant control plane | The core execution chain is `tenant_id -> workspace_id -> user_id -> session_id -> run_id`; files, artifacts, tools, skills, context, events, and audit attach to that chain as governed projections. |
| Governed execution | Claude Agent SDK is the current primary execution kernel; executors consume platform payloads and do not define platform schema. |
| Public/admin projections | Frontend and ordinary users consume only public or admin projections; executor private payloads remain private. |
| Operational evidence | Gate closure requires code, tests, review, issue/PR traceability, deployment evidence, and 211 runtime smoke where relevant. |
| Controlled expansion | High-risk sandbox, write tools, production concurrency increases, long-term memory, and platform-owned multi-run orchestration remain gate-blocked until evidence is complete. |

## 2. Non-Goals

| Non-goal | Reason |
| --- | --- |
| Single-user desktop assistant clone | The platform must solve tenant isolation, RBAC, quota, audit, and operations first. |
| Docker compose one-command delivery as the current acceptance gate | Current priority is the internal company deployment baseline; compose packaging is a later delivery milestone. |
| A second independent runtime/control plane | External projects can inform implementation, but ai-platform remains the source of truth for identity, tenancy, audit, quota, release, and governance. |
| Platform-owned parent/child multi-run orchestration as a current requirement | Claude Agent SDK is the execution layer. SDK-internal agent/subagent behavior is governed as one platform run unless a later gate explicitly reopens platform-level orchestration. |
| Long-term cross-session memory by default | Long-term memory must remain fail-closed until opt-out, retention, deletion, redaction, and tenant policy are proven. |

## 3. Document Authority

Use these documents together:

| Document | Responsibility |
| --- | --- |
| This PRD v2 | Product goal, architecture, reference boundaries, module contracts, gate roadmap, accepted first-stage baseline, and next-stage direction. |
| [Backend productization PRD](./2026-06-18-ai-platform-backend-phased-prd.md) | Backend B0-B6 sequencing after S1, backend gate-to-stage mapping, acceptance boundaries, and backend reference-code absorption rules. |
| [Frontend UI absorption PRD](./2026-06-18-librechat-frontend-ui-absorption-prd.md) | Frontend shell/UI absorption direction, acceptance boundaries, and UI reference boundaries. It must consume ai-platform public/admin projections and must not define backend authority. |
| [Technical acceptance matrix](./2026-06-11-ai-platform-tech-acceptance.md) | Current module state, phased target state, open-source absorption boundaries, and S1 acceptance standards. |
| [Foundation roadmap](../plans/2026-06-02-ai-platform-foundation-roadmap.md) | Execution sequencing, gate progress, current blockers, and next slices. It should not keep growing into a release-evidence ledger. |
| [Gate status snapshot](../../operations/ai-platform-gate-status.md) | Current gate state, remaining evidence, and operational risk summary. |
| [Foundation Alpha closure](../../operations/ai-platform-foundation-alpha-closure.md) | Compact S1 historical baseline record, accepted `380de6b` runtime-subject evidence, closure boundaries, and operator readiness commands. |
| [Guardrails](../../agent-rules/ai-platform-guardrails.md) | Implementation rules, source authority, verification policy, and security boundaries. The canonical path is `docs/agent-rules/ai-platform-guardrails.md`. |
| [GitHub issue and PR workflow](../../agent-rules/github-issue-pr-workflow.md) | Goal-sized work, gate closures, and defect closure use issue -> PR -> review -> merge -> deploy/smoke when required -> close issue with evidence. The canonical path is `docs/agent-rules/github-issue-pr-workflow.md`. |
| [Release evidence records](../../release-evidence/README.md) | Per-commit or per-gate evidence such as test output, image labels, smoke results, review notes, and runtime captures. |

If these sources disagree, stop broad implementation and first repair source
authority or gate wording.

This PRD v2 is the active product source for new ai-platform work. The
2026-05-29 PRD is retained as a migration appendix for detailed legacy contract
checks only where those checks do not contradict this PRD, the guardrails, the
roadmap, or the technical acceptance matrix. Future cleanup may convert the old
PRD into a short archive/redirect note after the remaining detailed checks are
moved into focused guardrail, contract, or acceptance documents.

### 3.1 Current Source Boundaries

- Local source is the current `ai-platform` repository root.
- 211 backend source is `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform`.
- 211 deploy composition target is `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform`.
- 211 frontend entry is `http://10.56.0.211:18001/`.
- 211 backend and worker containers are `ai-platform-api` and
  `ai-platform-worker`.
- Historical service layouts, old frontend entry points, temporary execution
  notes, and chat-only evidence are not product requirements.

## 4. External Reference Matrix

External projects are reference sources, not product authority. ai-platform owns
enterprise tenancy, governance, audit, and release closure.

Reference snapshots used for this draft:

| Source | Review snapshot |
| --- | --- |
| Codex CLI | local review clone `openai-codex-20260610-124630`, commit `00a25e1e0c6eecf076dcb989f4065c578c262ae7` |
| Poco Claw | local review clone `poco-claw-20260610-152718`, commit `7a61cb9f0e871f75f5623448f849f1e3d1958e35` |
| LambChat | current imported `frontend/web` source and migration notes; source baseline only |

| Source | What to absorb | What not to copy | Current role |
| --- | --- | --- | --- |
| ai-platform itself | Enterprise control plane, tenant/workspace/user/session boundaries, RBAC, queue admission, audit, release evidence. | Historical paths or stale implementation notes. | Product and architecture source of truth. |
| Codex CLI | Tool approval vocabulary, shell/sandbox permission model, skill/plugin packaging patterns, conversation/turn/context concepts, bounded context fragments, operator-grade CLI ergonomics. | Single-user identity assumptions, personal local memory model, non-enterprise tenancy. | Strong reference for tools, skills, sandbox vocabulary, and context mechanics. |
| Poco Claw | Claude Agent SDK platformization, persistent runtime registry, idle/sleep/resume/keepalive, team/server/channel collaboration UX, run drawer/playback/artifact surfaces, agent private state separation. | Enterprise tenant source of truth, unrestricted Docker socket assumptions, any fallback that bypasses ai-platform RBAC/quota/audit. | Strong reference for SDK runtime platformization and collaborative runtime UX. |
| LambChat | Existing `frontend/web` migration baseline, React/Vite shell, chat/task UI starting point. | Admin/runtime governance, tenancy, private payload access, model/channel/env-var management as product truth. | Frontend source baseline only. |

Historical references not listed above are intentionally omitted from the active
PRD route. Reopening any omitted project as an implementation reference requires
a focused issue, gate, and source-authority review.

Companion PRDs may list narrower implementation references for a bounded surface
such as backend sandboxing, memory, capacity, Skills governance, observability,
or frontend UI. Those references remain subordinate to this PRD's authority:
they can inform implementation, but they cannot define ai-platform identity,
tenancy, RBAC, audit, release evidence, runtime status, or gate closure.

### 4.1 Changes From The 2026-05-29 PRD

This PRD changes the old reference posture in three ways:

1. Codex CLI becomes the stronger reference for workspace, approval, shell/tool,
   event, and skill mechanics, but not for identity, tenant memory, audit, or
   enterprise source of truth.
2. Poco Claw remains the strongest reference for Claude Agent SDK runtime
   platformization and collaborative execution UX, but not for enterprise
   tenancy or security boundaries.
3. Omitted historical references must not create fields, gates, or adapters
   unless a future focused design explicitly reopens them.

### 4.2 Codex / Executor Kernel Boundary

Codex CLI and Claude Agent SDK can inform execution mechanics, but ai-platform
owns every enterprise contract around the execution kernel.

What ai-platform should absorb from Codex-style design:

1. **Workspace boundary**: every run gets a platform-issued workspace scoped by
   tenant, workspace, session, and run. Sandbox mode, filesystem access, cleanup,
   and network access are policy decisions owned by ai-platform.
2. **Approval policy**: shell commands, network access, MCP calls, write tools,
   and high-risk filesystem operations must flow through platform permission
   requests. Existing `run_tool_permission_requests` is the durable contract for
   `tool_id`, `tool_call_id`, risk, write capability, request payload, decision,
   expiry, and audit.
3. **Event sourcing**: plans, command starts, stdout/stderr summaries, diffs,
   tests, approval requests, decisions, tool results, artifacts, token/cost, and
   errors should be appended to `run_events` or related platform-owned ledgers.
   Final answers alone are not enough for replay, audit, or operations.
4. **Versioned skills**: each run uses published skill versions and
   `run_skill_snapshots`. The adapter must stage pinned snapshots and validate
   content hashes instead of freely reading a global mutable skills directory.

Non-negotiable boundary:

- Identity, session, tenant, workspace, memory policy, artifact ACL, audit,
  quota, release evidence, and source authority are always owned by ai-platform.
- Executor-private logs, Claude/Codex internal logs, raw command output, private
  subagent state, and private artifact metadata are never the platform source of
  truth.
- SDK-internal agents or subagents are not platform-visible multi-run
  orchestration. Current PRD scope treats them as execution-kernel behavior
  inside one governed platform run. Platform-visible parent/child run
  orchestration requires a future gate and must not leak into the current core
  field set.
- The product risk for SDK subagents is now capacity and governance, not basic
  availability. If each ordinary session can invoke SDK Agent/subagent fanout,
  the platform must prove worker, model-gateway, sandbox, artifact/event, and
  cost backpressure before broad exposure.
- Approval decisions must bind to exact `tool_call_id` or a stable request
  fingerprint. A broad "latest allow for this tool/action" lookup is not
  acceptable for replay or high-risk write operations.

## 5. Target Architecture

The target architecture is a layered control plane with narrow contracts between
layers.

```text
Company Auth / Tenant Boundary
  -> Platform API / Control Plane
       -> sessions, runs, queue, files, artifacts, skills, tools, memory, events, audit
       -> public projections and admin projections
  -> Worker Runtime
       -> tenant-aware lease/admission/backpressure
       -> executor adapter payloads
       -> sandbox lease and cleanup
  -> Claude Agent SDK Execution Kernel
       -> skills, tools, SDK-managed agent/subagent capability, filesystem/shell mediation
       -> private executor payloads
  -> Admin Runtime / Observability
       -> queue, run, sandbox, capacity, token/cost/latency/error, audit, release evidence
  -> Frontend Web
       -> ordinary-user task UI, permission cards, run playback, artifacts, memory/context management
       -> admin runtime and governance views
```

### 5.0 Core Field Set

The PRD core field set should stay small. These fields define the platform
execution coordinate system:

| Layer | Required fields | Purpose |
| --- | --- | --- |
| Isolation | `tenant_id`, `workspace_id`, `user_id` | Organization, workspace, and user boundary. |
| Conversation and execution | `session_id`, `run_id` | Session context and one concrete execution attempt. |
| Capability binding | `agent_id`, `skill_id`, `skill_version` | User-facing capability and pinned Skill package used by the run. |
| Governance attachments | `tool_call_id`, `context_snapshot_id`, `sandbox_lease_id`, `file_id`, `artifact_id`, `trace_id` | Permission, context, sandbox, input/output, and observability ledgers attached to the run. |

Fields such as `parent_run_id`, `child_run_id`, `dispatch_id`, or
`multi_agent_*` are not part of the current PRD core field set. They are
deferred orchestration fields and should not appear in ordinary-user product
contracts unless a later platform-level orchestration gate is explicitly
reopened.

### 5.1 Control Plane

The control plane owns all durable product contracts:

- Identity and tenancy: tenant, workspace, user, role, session.
- Run lifecycle: queued, leased, running, terminal states, retry, resume, cancel,
  checkpoint, and provenance links.
- Files and artifacts: user-visible projections, ACL, provenance, redaction.
- Tool governance: allow, ask, deny, user confirmation, admin policy, audit.
- Skill governance: version, release decision, dependency policy, provenance,
  review manifest, vulnerability/license evidence.
- Memory/context: opt-out, retention, redaction, delete/export readiness,
  context snapshots, provenance.
- Events and audit: public event playback, private executor payload separation,
  admin-only operational evidence.

### 5.2 Executor Runtime

The executor runtime is replaceable, but the platform contract is not. Claude
Agent SDK is the current primary execution kernel. It must receive bounded,
platform-produced payloads and return events, artifacts, tool requests, and
status through platform-owned repositories and projections.

For any task that uses multiple agents or subagents, the execution-layer route
is Claude Agent SDK. ai-platform governs that execution through platform-issued
payloads, pinned skill snapshots, permission policy, sandbox leases, event
sinks, artifact collection, and token/cost accounting. The platform should not
create a separate platform multi-run scheduler, parent/child run tree, or
external execution harness as part of the current roadmap.

The remaining product question is whether the target deployment can sustain SDK
Agent/subagent fanout when many sessions use it at the same time. That must be
answered through recorded capacity evidence covering queue depth, worker
parallelism, model-gateway timeout/backpressure, sandbox pressure, artifact/event
volume, token/cost accounting, and cleanup. Until that evidence exists, SDK
subagent use stays governed and bounded even though the execution capability is
available.

Executors must not:

- Create platform schema.
- Expose private payloads to ordinary-user frontend code.
- Bypass tenant quota, active-run admission, tool policy, or sandbox lease.
- Turn SDK-internal agent/subagent fanout into hidden unbounded work outside
  platform quotas, event sinks, artifact collection, and cost accounting.

### 5.3 Frontend

The frontend is a same-repository product surface under `frontend/web`. It must
consume only ai-platform public/admin projections. It must not read executor
private payloads, raw storage keys, sandbox work directories, command
fingerprints, resource-limit internals, secret-like env values, or hidden
runtime metadata.

Frontend delivery should move toward backend/worker/frontend same-commit
traceability and separate deployable images. Packaged frontend smoke remains a
gate evidence item, not a prerequisite for every local docs change.

## 6. Current Module Weaknesses

This table reflects current working-tree and documentation review. It is a risk
map, not a gate-closure claim.

| Area | Existing foundation | Thin or missing part | Stage impact |
| --- | --- | --- | --- |
| G0-G1 source authority / security | Source docs, guardrails, source-authority tests, frontend source migration, and accepted `380de6b` S1 source/runtime/Auth/RBAC evidence. | Latest current-source runtime parity and Foundation Runtime concurrency evidence must be refreshed whenever readiness reports `runtime_rollout_required`. | Blocks current-source S1 completion and next-stage unrestricted claims, but does not erase the historical S1 baseline. |
| Auth/session/RBAC | Trusted header, company login path, admin role, gateway secret tests, and accepted `380de6b` 211 Auth/RBAC/tenant/redaction smoke. | Keep cross-tenant and ordinary/admin paths under regression; rerun 211 smoke for runtime-affecting source changes before current-source claims. | Blocks ordinary-user rollout claims. |
| Control plane schema | Session/run/file/artifact/skill/tool/memory/event/audit contracts and indexes exist. | Wide blast radius; executor schema drift must stay guarded by regression tests. | Requires full regression before PR/deploy. |
| Queue/worker/concurrency | Tenant-aware queue lease, worker maintenance, active-run admission, bounded metadata, and accepted `380de6b` Foundation Runtime concurrency evidence. | The GitHub #21 issue is closed, but the capacity-upgrade evidence gate still lacks recorded seven-gate load evidence; latest current-source Foundation Runtime concurrency evidence must be refreshed when readiness requires it. | Blocks high-concurrency gate and current-source S1 completion when `foundation_runtime_concurrency_evidence` is listed as a readiness blocker. |
| Admin Runtime / Observability | Admin overview, capacity/backpressure projections, readiness tools. | Per-surface latency, model-gateway backpressure, golden-set eval, alert delivery, trace/export, release-evidence runtime acceptance remain partial. | Blocks beta operations. |
| Sandbox | Lease/cleanup/provider abstractions and tests exist; fake provider remains useful locally. | Docker provider hardening, egress/quota policy, orphan cleanup evidence, container security options, 211 Docker smoke. | Blocks high-risk sandbox/tool expansion. |
| Memory/context | Session-bound memory, opt-out, retention cleanup, redaction, admin inventory. | Document-centric context pack runtime persistence/injection, frontend provenance, long-term memory policy closure. | Long memory remains fail-closed. |
| Tool permission | Allow/ask/deny taxonomy, user permission card, admin policy/history routes, and source-route tests for admin bulk-review runtime controls. | Legacy frontend route remap/enforcement, admin bulk-review visual acceptance, and admin bulk-review 211 acceptance. | Blocks ordinary-user write-tool expansion. |
| Skill governance | SDK staging, pin mismatch checks, used-skill recording, release-readiness contracts, and source-route tests for Admin Skill release dashboard runtime controls. | SBOM/signed package, license/vulnerability review, reviewed evidence files, Admin Skill release visual acceptance, and Admin Skill release 211 acceptance. | Blocks production-grade skill release. |
| Frontend web | `frontend/web`, projection audit, lint/type/build scripts, release traceability and CI direction. | Projection audit still has policy gaps; inactive legacy model/channel/env-var sources remain quarantined; packaged smoke incomplete. | Blocks G6/G9 rollout closure. |
| Artifacts/playback/events | Artifact cards, run playback, SSE/event projections, redaction tests. | New event or artifact types can bypass projection discipline if tests are not added. | Requires continuous projection audit. |
| Advanced SDK subagent / long-task patterns | Claude Agent SDK is the execution path; the platform governs one run with payload, permission, sandbox, events, artifacts, and cost ledgers. | Platform-owned parent/child run orchestration and dispatcher work is deferred and must not shape current ordinary-user contracts; the active open risk is per-session SDK subagent fanout load. | Do not build a new harness. Broad exposure waits for capacity, model-gateway, sandbox, and observability evidence for SDK subagent fanout. |

### 6.1 Detailed Contract Appendix During Migration

This active PRD intentionally keeps the main product source concise. Detailed
contract checks from the 2026-05-29 PRD remain binding as a migration appendix
where they do not contradict the active source-authority set, especially:

- Non-admin access to admin routes must fail closed.
- Cross-tenant reads for run, file, artifact, memory, queue, and sandbox state
  must fail closed.
- File upload and artifact download must enforce same-tenant and authorized-user
  access.
- Disabled or unpublished skills must not be staged into new runs.
- Write-capable tools must not execute without a valid decision.
- `allow_once` must not be replayable across unrelated `tool_call_id` values or
  request fingerprints.
- SSE/event replay must preserve order and redaction.
- Memory policy disablement must prevent reads and writes in the affected scope.
- Sandbox lease renewal, release, cleanup, and container stop/remove must derive
  targets from platform-owned lease scope, not from user-controlled payloads.
- Run cancel must prevent unintended artifact or tool side effects.
- Executor responses must not expose private payloads to public projections.
- Artifact preview must stay allowlisted.
- Admin audit must not expose ordinary-user secrets or executor-private payloads.

Future source-authority cleanup should move these checks into either a compact
appendix in this file or a dedicated guardrail/contract checklist referenced by
the roadmap and source-authority tests.

## 7. Gate Roadmap

The roadmap should use G0-G10 as the single gate language. Older P0/P1/P2 names
can remain only as historical aliases in release notes or archived plans.

| Gate | Goal | Exit evidence |
| --- | --- | --- |
| G0-G1 Source Authority / Security Baseline | Local source, 211 source, deploy composition, runtime labels, frontend source, docs, company auth/session, RBAC, tenant/workspace/user boundaries, redaction, and deny-by-default tool posture point to the same authority. | Source-authority tests, same-commit evidence, 211 label/source parity, stale-path reconciliation, 211 auth/RBAC/tenant smoke, redaction checks, gateway/header enforcement tests. |
| G2-G4 Control Plane MVP | Core platform contracts are stable and executor-independent. | Contract tests, repository tests, route tests, schema drift checks, full regression before merge/deploy. |
| G5 Run Lifecycle / Worker Runtime V1 | Queue, lease, heartbeat, retry, dead-letter, cancel, resume, checkpoint, idempotency, admission, and backpressure are operational. | Tenant-aware tests, bounded metadata proof, recorded capacity-upgrade evidence, Foundation Runtime concurrency evidence, and 211 worker smoke. |
| G6 Tool / Skill / Memory Governance | Tool, skill, and memory policies are enforceable, auditable, and fail-closed. | Policy tests, frontend projection audit, skill review evidence, memory retention/delete/redaction evidence, admin acceptance. |
| G7 Sandbox / Resource Hardening | Docker provider, network/egress, quota, cleanup, and container security are proven. | Docker-capable host smoke, security-option evidence, orphan cleanup proof, no default overexposure. |
| G8 Deferred Platform Multi-Run Gate | Deferred parking-lot for platform-level parent/child multi-run orchestration. Historical evidence and appendices may mention the old title "G8 Multi-Agent Controlled Beta"; do not use that title for current status, and do not read it as ordinary-user platform-level multi-run product exposure. Current Claude Agent SDK Agent/subagent fanout capability stays inside Claude Agent SDK and is governed as one platform run. | Do not reopen to build a separate harness. B3 SDK subagent load evidence may inform a later G8 decision, but it does not itself reopen or close G8. Reopen G8 only through a focused platform-orchestration issue with tenant quota/backpressure design, event/artifact/cancel semantics, and no ordinary-user exposure before prior gates. |
| G9 Observability / Quality / Ops | Admin Runtime, cost/token/latency/error taxonomy, golden-set eval, trace/export, alerts, and release evidence are operational. | Dashboard acceptance, recorded load evidence, model-gateway evidence, alert calibration, trace/export smoke, release evidence export. |
| G10 Internal Beta / Department Rollout | 1-2 real internal workflows run with owners, cost/quality/audit/rollback evidence, and support model. | Workflow owner signoff, 211 smoke, rollback proof, issue/PR/release evidence, documented limits. |

## 8. First Stage Accepted Baseline

The first stage is named **Foundation Alpha / internal controlled foundation
loop**. Its historical controlled baseline is accepted for the `380de6b`
runtime subject. The compact closure record is
`docs/operations/ai-platform-foundation-alpha-closure.md`
([Foundation Alpha closure](../../operations/ai-platform-foundation-alpha-closure.md));
the operator-facing readiness summary remains:

```powershell
python tools\foundation_alpha_readiness.py --format json
```

S1 baseline acceptance means the platform has reviewed evidence for the internal
POC loop at the accepted runtime subject, including source/runtime relation, 211 runtime POC smoke,
Auth/RBAC/tenant/redaction smoke, governed skill runs, permission and projection
controls, memory/context fail-closed controls, Admin Runtime visibility,
Foundation Runtime concurrency correctness, artifact ACL isolation, pinned skill
snapshots, and release-evidence/alert-trace runtime acceptance.

Current-source completion is a separate status check. If the readiness tool
reports `foundation_alpha_stage_complete=false`,
`foundation_alpha_stage_status=runtime_rollout_required`, or
`stage_acceptance_blockers` containing `foundation_runtime_concurrency_evidence`,
report the latest source as **S2-0 latest-main evidence refresh required**,
not as current-source S1 complete.

The accepted baseline is bounded. It does not:

1. Raise production concurrency defaults.
2. Broaden ordinary-user platform-level multi-run orchestration exposure.
3. Claim Docker sandbox hardening.
4. Permit department rollout.
5. Enable long-term cross-session memory by default.
6. Close packaged frontend image release acceptance.
7. Close signed Skill package, SBOM, license, or vulnerability evidence.

The first stage was accepted after:

1. G0-G1 are basic-operational and backed by fresh local plus 211 evidence.
2. G2-G4 contracts are stable enough that frontend, worker, and executor changes
   can be reviewed against platform-owned contracts.
3. G5 is operational for controlled internal workloads, but this does not mean
   high-concurrency readiness, department beta, or ordinary-user platform-level
   multi-run orchestration exposure. High-concurrency default increases remain blocked until the
   capacity-upgrade evidence gate has recorded load evidence.
4. G6 has a usable fail-closed baseline: ordinary users see permission cards and
   safe public projections; admins can inspect policy and evidence; long-term
   memory and production skill release remain controlled.
5. G7 keeps high-risk sandbox exposure blocked unless Docker-provider hardening
   and 211 smoke are proven.
6. G9 provides enough Admin Runtime visibility for controlled operation, but does
   not claim full beta readiness until alerting, golden-set, trace/export, model
   gateway, and release evidence are runtime-accepted.
7. G8 remains a deferred platform-owned parent/child multi-run parking lot, and
   ordinary-user exposure remains blocked. SDK Agent/subagent fanout is governed
   inside one platform run and belongs to B3 capacity evidence until a future
   focused G8 issue explicitly reopens platform orchestration.
8. G10 remains planning-only until workflow-owner rollout evidence exists.

In one sentence:

> Foundation Alpha means the enterprise multi-tenant Agent platform has an
> auditable, regression-tested, 211-verifiable control-plane loop; production
> concurrency increases, high-risk sandbox/tool expansion, long-term memory by
> default, and platform-level multi-run orchestration exposure remain
> gate-blocked.

## 9. First-Stage Deliverables

| Deliverable | Required state |
| --- | --- |
| PRD/roadmap/guardrails | G0-G10 language unified; P0/P1/P2 only historical; external reference boundaries updated. |
| Technical acceptance matrix | [Technical acceptance matrix](./2026-06-11-ai-platform-tech-acceptance.md) lists each module's current state, staged target, open-source reference source, and S1 acceptance standard; Foundation Alpha cannot be accepted if the matrix contradicts PRD, roadmap, guardrails, or gate status. |
| Issue/PR workflow | [GitHub issue and PR workflow](../../agent-rules/github-issue-pr-workflow.md) is linked from this PRD; goal-sized work opens or references issues; closure uses PR/review/tests/211 evidence where required. |
| Foundation Alpha readiness | `tools/foundation_alpha_readiness.py --format json` is the operator-facing S1 summary. It separates exact current-source verification from runtime-relevant source coverage: `current_source_verified_by_running_runtime=true` and `controlled_poc_loop_verified_for_current_source=true` require the running image to match the current source tree, while `runtime_relevant_source_verified_by_running_runtime=true` only means later docs/tests/evidence/readiness records are outside the running image. If `current_source_exact_runtime_commit_match=false`, the summary must show `runtime_current_for_runtime_relevant_source` with empty runtime-affecting changes before operators reuse the recorded controlled core POC loop evidence. The accepted historical S1 baseline records `foundation_alpha_stage_complete=true` for its accepted subject; latest current-source claims require a fresh readiness result with no stage acceptance blockers. Production claims, capacity default increases, Docker sandbox hardening, platform-level multi-run orchestration exposure, and department rollout remain blocked by later gates even after S1 baseline acceptance. |
| Frontend source | `frontend/web` has reproducible install/lint/type/build or a precise blocker; projection audit has no active private-payload violations. |
| Admin Runtime | Queue/run/sandbox/capacity/backpressure overview exists and has 211 smoke evidence. |
| Capacity evidence gate | GitHub #21 is closed, but the capacity-upgrade evidence gate still needs recorded seven-gate load evidence; default production concurrency is not raised without that evidence. |
| Tool permission | User confirmation card and admin policy flow operate through public/admin projections. |
| Memory/context | Opt-out, retention, redaction, delete/export readiness, and context provenance are documented and tested for the current scope. |
| Skill governance | Release/readiness manifests exist; production skill release is blocked without SBOM/signature/license/vulnerability evidence. |
| Sandbox | Fake provider remains local/test; Docker provider hardening is a separate G7 evidence gate. |
| Advanced SDK subagent / long-task patterns | Claude Agent SDK may use internal agent/subagent capability under one governed platform run. Platform-owned parent/child run orchestration stays out of the current deliverable set; the next evidence question is deployment capacity when many sessions invoke SDK subagents. |

## 10. Change And Review Workflow For This PRD

This PRD is active, but product-source changes still require review discipline:

1. Self-review for stale project references, contradictions, and missing module
   risks.
2. Independent read-only review for large or gate-moving changes: one reviewer
   checks product/reference-source consistency, another checks module/gate
   realism against code.
3. User or product-owner review for business direction, first-stage target, or
   reference-source changes.
4. Revision pass that keeps the PRD, technical acceptance matrix, roadmap,
   guardrails, gate status, and source-authority tests aligned.
5. If old PRD appendix content is migrated, preserve the detailed contract
   intent in a focused guardrail, acceptance, or release-evidence document
   before deleting the old wording.

## 11. Immediate Next Roadmap Slices

Recommended order after S1 acceptance:

1. **S2-0 latest-main evidence refresh**: roll out the latest main/current
   source to the target runtime and refresh Foundation Alpha readiness,
   Foundation Runtime concurrency evidence, and 211 smoke until the readiness
   summary no longer reports `runtime_rollout_required` or
   `foundation_runtime_concurrency_evidence` as a stage blocker.
2. **S2 governance and operations baseline**: keep this PRD, the technical
   acceptance matrix, roadmap, guardrails, gate status, and closure note aligned
   while moving dynamic evidence into release-evidence records.
3. **G7 sandbox hardening**: integrate Docker-provider execution into the real
   SDK skill path with security options, egress/quota policy, orphan cleanup,
   and Docker-capable 211 smoke before high-risk sandbox exposure.
4. **G6 skill and tool governance**: complete signed Skill package or SBOM
   evidence, dependency/license/vulnerability review, policy dashboard
   acceptance, and exact permission enforcement for write/high-risk tools.
5. **Capacity-upgrade evidence gate**: complete recorded load evidence for the
   seven required gates, including SDK Agent/subagent fanout pressure when
   session workflows enable it; keep production defaults unchanged until
   evidence passes.
6. **G9 Admin Runtime acceptance**: dashboard/operator acceptance, alert
   delivery calibration, model-gateway backpressure evidence, trace/export, and
   release-evidence runtime export.
7. **Advanced Claude Agent SDK task-pattern planning**: only after prior gates,
   choose 1-2 internal workflows with owners. Use Claude Agent SDK for
   execution-layer agent/subagent capability inside governed runs. Do not
   introduce platform-owned parent/child orchestration unless a focused future
   G8 gate reopens it. The first proof should be capacity and governance
   evidence for SDK subagent fanout, not a new agent harness.
