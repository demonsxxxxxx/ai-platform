# AI Platform Product PRD v2

> Status: active product PRD.
>
> Purpose: restate the ai-platform product goal, architecture, reference-source
> boundaries, module weaknesses, gate roadmap, and first-stage completion target.
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
| Multi-tenant control plane | Tenant, workspace, user, session, run, file, artifact, tool, skill, memory, event, and audit are platform-owned contracts. |
| Governed execution | Claude Agent SDK is the current primary execution kernel; executors consume platform payloads and do not define platform schema. |
| Public/admin projections | Frontend and ordinary users consume only public or admin projections; executor private payloads remain private. |
| Operational evidence | Gate closure requires code, tests, review, issue/PR traceability, deployment evidence, and 211 runtime smoke where relevant. |
| Controlled expansion | High-risk sandbox, write tools, production concurrency increases, and ordinary-user multi-agent exposure remain gate-blocked until evidence is complete. |

## 2. Non-Goals

| Non-goal | Reason |
| --- | --- |
| Single-user desktop assistant clone | The platform must solve tenant isolation, RBAC, quota, audit, and operations first. |
| Docker compose one-command delivery as the current acceptance gate | Current priority is the internal company deployment baseline; compose packaging is a later delivery milestone. |
| A second independent runtime/control plane | External projects can inform implementation, but ai-platform remains the source of truth for identity, tenancy, audit, quota, release, and governance. |
| Ordinary-user multi-agent beta before foundation gates | G8/G10 depends on G5/G6/G7/G9 evidence and must stay feature-flagged. |
| Long-term cross-session memory by default | Long-term memory must remain fail-closed until opt-out, retention, deletion, redaction, and tenant policy are proven. |

## 3. Document Authority

Use these documents together:

| Document | Responsibility |
| --- | --- |
| This PRD v2 | Product goal, architecture, reference boundaries, module contracts, gate roadmap, first-stage target. |
| [Technical acceptance matrix](./2026-06-11-ai-platform-tech-acceptance.md) | Current module state, phased target state, open-source absorption boundaries, and S1 acceptance standards. |
| [Foundation roadmap](../plans/2026-06-02-ai-platform-foundation-roadmap.md) | Execution sequencing, gate progress, current blockers, and next slices. It should not keep growing into a release-evidence ledger. |
| [Gate status snapshot](../../operations/ai-platform-gate-status.md) | Current gate state, remaining evidence, and operational risk summary. |
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
| DeerFlow 2.0 | local review clone `deer-flow-20260610-prd-review`, commit `a57d05fe0a83551e928c5832183323cd29456687` |
| LambChat | current imported `frontend/web` source and migration notes; source baseline only |
| new-api | model-gateway reference only; not a platform authority source; refresh separately before model-gateway implementation work |
| AgentScope | prior PRD reference now downgraded to concept-only; no active snapshot is required unless reopened by a new gate |

| Source | What to absorb | What not to copy | Current role |
| --- | --- | --- | --- |
| ai-platform itself | Enterprise control plane, tenant/workspace/user/session boundaries, RBAC, queue admission, audit, release evidence. | Historical paths or stale implementation notes. | Product and architecture source of truth. |
| Codex CLI | Tool approval vocabulary, shell/sandbox permission model, skill/plugin packaging patterns, conversation/turn/context concepts, bounded context fragments, operator-grade CLI ergonomics. | Single-user identity assumptions, personal local memory model, non-enterprise tenancy. | Strong reference for tools, skills, sandbox vocabulary, and context mechanics. |
| Poco Claw | Claude Agent SDK platformization, persistent runtime registry, idle/sleep/resume/keepalive, team/server/channel collaboration UX, run drawer/playback/artifact surfaces, agent private state separation. | Enterprise tenant source of truth, unrestricted Docker socket assumptions, any fallback that bypasses ai-platform RBAC/quota/audit. | Strong reference for SDK runtime platformization and collaborative runtime UX. |
| DeerFlow 2.0 | Long-horizon agent harness, per-user/per-thread isolation techniques, memory scoping, guardrail middleware, subagent/concurrency concepts, research/report product patterns. | A second long-task control plane, direct replacement for ai-platform worker/runtime, uncontrolled MCP/shell exposure. | Concept reference for long-horizon workflows after foundation gates. |
| LambChat | Existing `frontend/web` migration baseline, React/Vite shell, chat/task UI starting point. | Admin/runtime governance, tenancy, private payload access, model/channel/env-var management as product truth. | Frontend source baseline only. |
| new-api / model gateway patterns | Model gateway routing, token/cost/latency accounting, upstream-provider operational concepts. | Platform RBAC, tenant audit, or memory/tool governance. | Reference for model gateway integration and observability. |
| AgentScope | Agent/service/skill/workspace vocabulary and adapter ideas. | Product authority for enterprise auth, RBAC, artifact ACL, or memory policy. | Downgraded concept reference unless a concrete adapter gate is reopened. |

### 4.1 Changes From The 2026-05-29 PRD

This PRD changes the old reference posture in three ways:

1. Codex CLI becomes the stronger reference for workspace, approval, shell/tool,
   event, and skill mechanics, but not for identity, tenant memory, audit, or
   enterprise source of truth.
2. AgentScope is downgraded from active adapter direction to concept reference.
   Reopening an AgentScope adapter requires a new issue, gate, and source
   authority review.
3. DeerFlow 2.0 remains a long-horizon workflow and subagent/concurrency concept
   reference. It is not a second ai-platform runtime, scheduler, or memory
   authority.

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
- CLI-internal subagents are not automatically platform multi-agent runs.
  Platform multi-agent scheduling requires parent/child run ledger, admission,
  tenant quota, backpressure, event projection, and cancellation semantics.
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
       -> skills, tools, filesystem/shell mediation
       -> private executor payloads
  -> Admin Runtime / Observability
       -> queue, run, sandbox, capacity, token/cost/latency/error, audit, release evidence
  -> Frontend Web
       -> ordinary-user task UI, permission cards, run playback, artifacts, memory/context management
       -> admin runtime and governance views
```

### 5.1 Control Plane

The control plane owns all durable product contracts:

- Identity and tenancy: tenant, workspace, user, role, session.
- Run lifecycle: queued, leased, running, terminal states, retry, resume, cancel,
  checkpoint, parent/child relationships.
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

Executors must not:

- Create platform schema.
- Expose private payloads to ordinary-user frontend code.
- Bypass tenant quota, active-run admission, tool policy, or sandbox lease.
- Turn multi-agent child fanout into hidden unbounded work.

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
| G0-G1 source authority / security | Source docs, guardrails, source-authority tests, frontend source migration. | Fresh 211 parity evidence, AD/session/RBAC/tenant isolation smoke, stale deploy-path reconciliation. | Blocks first-stage closure evidence. |
| Auth/session/RBAC | Trusted header, company login path, admin role, gateway secret tests. | Login still risks default-tenant assumptions; needs cross-tenant and ordinary/admin 211 smoke. | Blocks ordinary-user rollout claims. |
| Control plane schema | Session/run/file/artifact/skill/tool/memory/event/audit contracts and indexes exist. | Wide blast radius; executor schema drift must stay guarded by regression tests. | Requires full regression before PR/deploy. |
| Queue/worker/concurrency | Tenant-aware queue lease, worker maintenance, active-run admission, bounded metadata. | #21 recorded load evidence missing; production concurrency defaults must not be raised without load proof. | Blocks high-concurrency gate. |
| Admin Runtime / Observability | Admin overview, capacity/backpressure projections, readiness tools. | Per-surface latency, model-gateway backpressure, golden-set eval, alert delivery, trace/export, release-evidence runtime acceptance remain partial. | Blocks beta operations. |
| Sandbox | Lease/cleanup/provider abstractions and tests exist; fake provider remains useful locally. | Docker provider hardening, egress/quota policy, orphan cleanup evidence, container security options, 211 Docker smoke. | Blocks high-risk sandbox/tool expansion. |
| Memory/context | Session-bound memory, opt-out, retention cleanup, redaction, admin inventory. | Document-centric context pack runtime persistence/injection, frontend provenance, long-term memory policy closure. | Long memory remains fail-closed. |
| Tool permission | Allow/ask/deny taxonomy, user permission card, admin policy/history routes. | Legacy frontend route remap/enforcement, admin bulk/runtime acceptance, visual and 211 acceptance. | Blocks ordinary-user write-tool expansion. |
| Skill governance | SDK staging, pin mismatch checks, used-skill recording, release-readiness contracts. | SBOM/signed package, license/vulnerability review, reviewed evidence files, Admin dashboard acceptance. | Blocks production-grade skill release. |
| Frontend web | `frontend/web`, projection audit, lint/type/build scripts, release traceability and CI direction. | Projection audit still has policy gaps; inactive legacy model/channel/env-var sources remain quarantined; packaged smoke incomplete. | Blocks G6/G9 rollout closure. |
| Artifacts/playback/events | Artifact cards, run playback, SSE/event projections, redaction tests. | New event or artifact types can bypass projection discipline if tests are not added. | Requires continuous projection audit. |
| Multi-agent runtime | Dispatcher, child admission, reconcile/rollup/cancel work exists behind controls. | Depends on G5/G6/G7/G9; not ready for ordinary users. | Feature-flag only before later gate. |

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
| G5 Run Lifecycle / Worker Runtime V1 | Queue, lease, heartbeat, retry, dead-letter, cancel, resume, checkpoint, idempotency, admission, and backpressure are operational. | Tenant-aware tests, bounded metadata proof, #21 load/capacity evidence, 211 worker smoke. |
| G6 Tool / Skill / Memory Governance | Tool, skill, and memory policies are enforceable, auditable, and fail-closed. | Policy tests, frontend projection audit, skill review evidence, memory retention/delete/redaction evidence, admin acceptance. |
| G7 Sandbox / Resource Hardening | Docker provider, network/egress, quota, cleanup, and container security are proven. | Docker-capable host smoke, security-option evidence, orphan cleanup proof, no default overexposure. |
| G8 Multi-Agent Controlled Beta | Multi-agent dispatch, handoff, child reconciliation, parent rollup/cancel, quota, and backpressure are controlled. | Feature-flagged internal proof, tenant quota evidence, no ordinary-user exposure before prior gates. |
| G9 Observability / Quality / Ops | Admin Runtime, cost/token/latency/error taxonomy, golden-set eval, trace/export, alerts, and release evidence are operational. | Dashboard acceptance, recorded load evidence, model-gateway evidence, alert calibration, trace/export smoke, release evidence export. |
| G10 Internal Beta / Department Rollout | 1-2 real internal workflows run with owners, cost/quality/audit/rollback evidence, and support model. | Workflow owner signoff, 211 smoke, rollback proof, issue/PR/release evidence, documented limits. |

## 8. First Stage Definition

The first stage should be named **Foundation Alpha / internal controlled
foundation loop**.

The first stage is reached when:

1. G0-G1 are basic-operational and backed by fresh local plus 211 evidence.
2. G2-G4 contracts are stable enough that frontend, worker, and executor changes
   can be reviewed against platform-owned contracts.
3. G5 is operational for controlled internal workloads, but this does not mean
   high-concurrency readiness, department beta, or ordinary-user multi-agent
   exposure. High-concurrency default increases remain blocked until #21
   recorded load evidence is complete.
4. G6 has a usable fail-closed baseline: ordinary users see permission cards and
   safe public projections; admins can inspect policy and evidence; long-term
   memory and production skill release remain controlled.
5. G7 keeps high-risk sandbox exposure blocked unless Docker-provider hardening
   and 211 smoke are proven.
6. G9 provides enough Admin Runtime visibility for controlled operation, but does
   not claim full beta readiness until alerting, golden-set, trace/export, model
   gateway, and release evidence are runtime-accepted.
7. G8/G10 remain feature-flagged or planning-only for ordinary users.

In one sentence:

> Foundation Alpha means the enterprise multi-tenant Agent platform has an
> auditable, regression-tested, 211-verifiable control-plane loop; production
> concurrency increases, high-risk sandbox/tool expansion, long-term memory by
> default, and ordinary-user multi-agent exposure remain gate-blocked.

## 9. First-Stage Deliverables

| Deliverable | Required state |
| --- | --- |
| PRD/roadmap/guardrails | G0-G10 language unified; P0/P1/P2 only historical; external reference boundaries updated. |
| Technical acceptance matrix | [Technical acceptance matrix](./2026-06-11-ai-platform-tech-acceptance.md) lists each module's current state, staged target, open-source reference source, and S1 acceptance standard; Foundation Alpha cannot be accepted if the matrix contradicts PRD, roadmap, guardrails, or gate status. |
| Issue/PR workflow | [GitHub issue and PR workflow](../../agent-rules/github-issue-pr-workflow.md) is linked from this PRD; goal-sized work opens or references issues; closure uses PR/review/tests/211 evidence where required. |
| Foundation Alpha readiness | `tools/foundation_alpha_readiness.py --format json` is the operator-facing S1 summary. It separates exact current-source verification from runtime-relevant source coverage: `current_source_verified_by_running_runtime=true` and `controlled_poc_loop_verified_for_current_source=true` require the running image to match the current source tree, while `runtime_relevant_source_verified_by_running_runtime=true` only means later docs/tests/evidence/readiness records are outside the running image. If `current_source_exact_runtime_commit_match=false`, the summary must show `runtime_current_for_runtime_relevant_source` with empty runtime-affecting changes before operators reuse the recorded controlled core POC loop evidence. Production claims, capacity default increases, Docker sandbox hardening, ordinary-user multi-agent exposure, and stage closure remain blocked until `foundation_alpha_stage_complete=true`. |
| Frontend source | `frontend/web` has reproducible install/lint/type/build or a precise blocker; projection audit has no active private-payload violations. |
| Admin Runtime | Queue/run/sandbox/capacity/backpressure overview exists and has 211 smoke evidence. |
| Capacity baseline | #21 has a recorded evidence plan or harness; default production concurrency is not raised without evidence. |
| Tool permission | User confirmation card and admin policy flow operate through public/admin projections. |
| Memory/context | Opt-out, retention, redaction, delete/export readiness, and context provenance are documented and tested for the current scope. |
| Skill governance | Release/readiness manifests exist; production skill release is blocked without SBOM/signature/license/vulnerability evidence. |
| Sandbox | Fake provider remains local/test; Docker provider hardening is a separate G7 evidence gate. |
| Multi-agent | Internal feature flags only; no ordinary-user expansion before G5/G6/G7/G9 closure. |

## 10. Change And Review Workflow For This PRD

This PRD is active, but product-source changes still require review discipline:

1. Self-review for stale project references, contradictions, and missing module
   risks.
2. Multi-agent read-only review for large or gate-moving changes: one reviewer
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

Recommended order after PRD review:

1. **Docs source-authority cleanup**: make this PRD v2 the active product source,
   slim the foundation roadmap into gate state and next decisions, and move
   release evidence to evidence-specific files.
2. **G0-G1 evidence refresh**: run fresh 211 source/deploy/runtime label parity
   checks plus auth/session/RBAC/tenant/redaction smoke.
3. **#21 capacity baseline**: complete recorded load evidence for the seven
   required gates; keep production defaults unchanged until evidence passes.
4. **G6 governance closure slice**: close frontend route remap/enforcement gaps,
   produce reviewed skill release evidence, and prove memory delete/retention
   behavior.
5. **G9 Admin Runtime acceptance**: dashboard/operator acceptance, alert
   delivery calibration, model-gateway backpressure evidence, trace/export, and
   release-evidence runtime export.
6. **G7 sandbox hardening**: Docker provider security and 211 smoke on a
   Docker-capable host.
7. **G8/G10 planning**: only after prior gates, choose 1-2 internal workflows
   with owners and keep multi-agent exposure controlled.
