# AI Platform Backend Productization PRD

> Status: proposed backend companion PRD.
>
> Scope: restate the backend productization route after Foundation Alpha/S1.
> Frontend UI absorption is handled by
> `docs/superpowers/specs/2026-06-18-librechat-frontend-ui-absorption-prd.md`;
> this document only defines backend contracts, gates, acceptance boundaries,
> and reference-code absorption for the backend.
>
> This document is not gate-closure evidence. Every stage below still requires
> the repository workflow: issue -> branch/PR -> review -> merge -> 211 smoke
> when runtime evidence is required -> issue closure with evidence.

## 0. Document Relationship And Authority

This backend PRD narrows the backend productization route after the accepted S1
historical baseline. It does not replace
`docs/superpowers/specs/2026-06-10-ai-platform-product-prd-v2.md`; it expands
that PRD's S2/S3 backend requirements into staged backend work packages,
acceptance boundaries, and implementation reference sources.

Use the documents as follows:

| Document | Role |
| --- | --- |
| Product PRD v2 | Product direction, active reference boundaries, G0-G10 roadmap, and S1 historical-baseline wording. |
| This backend PRD | Backend B0-B6 sequencing, gate-to-stage mapping, backend acceptance boundaries, and backend implementation reference projects. |
| Technical acceptance matrix | Module-level current/S1/S2/S3/S4 acceptance text and regression expectations. |
| Gate status snapshot | Current status, current blockers, and evidence caveats; not gate closure by itself. |
| Release evidence records | Runtime evidence, smoke output, reviewed evidence bundles, and redacted proof artifacts. |
| Frontend UI absorption PRD | Frontend shell/UI direction only; backend remains ai-platform public/admin projection authority. |

If this backend PRD and product PRD v2 disagree, stop feature work and repair
the PRD wording first. Do not use this companion PRD to claim a gate is closed
without the matching gate evidence.

## 1. Product Decision

The backend route after S1 is productization of the ai-platform control plane,
not a new agent harness and not a second backend. Claude Agent SDK remains the
execution layer. ai-platform owns identity, tenancy, run lifecycle, queue,
sandbox policy, memory, skills, tools, files, artifacts, events, audit, model
gateway limits, cost, and release evidence.

The first backend productization wave focuses on four P0 capabilities:

1. Memory/context is usable under tenant, session, retention, redaction, and
   delete/export policy.
2. Real sandbox execution is usable instead of treating `fake` as production.
3. Worker capacity can be increased only with queue, model-gateway,
   sandbox-pressure, and cost evidence.
4. Skills management is usable for upload, versioning, release, rollback,
   dependency review, permission scope, and pinned run snapshots.

The supporting backend capabilities are model-gateway/cost controls,
artifact/file governance, exact tool permission policy, and Admin Runtime
observability. Without these, the four P0 capabilities cannot be safely exposed.

## 2. Non-Goals

| Non-goal | Reason |
| --- | --- |
| Replacing ai-platform backend with LibreChat, Dify, LangGraph, or another platform | External projects may inform implementation, but ai-platform remains the source of truth for enterprise identity, tenancy, audit, and release evidence. |
| Building a separate platform-level parent/child agent harness now | SDK Agent/subagent behavior is an execution-layer capability inside one governed platform run. Platform-owned multi-run orchestration remains deferred. |
| Raising production worker or model concurrency by configuration only | Capacity increases require recorded load evidence, backpressure, rate limits, cleanup evidence, and rollback path. |
| Treating fake sandbox as production | `fake` remains useful for tests/local flows only. Production-grade sandbox requires Docker-provider or equivalent hardening evidence. |
| Enabling long-term memory by default | Long-term memory must be opt-in or policy-bound until opt-out, retention, delete/redaction, export, and cross-tenant deny paths are proven. |
| Shipping Skills as mutable filesystem folders | Production Skill execution uses reviewed versions, release state, dependency policy, pinned snapshots, and used-skill evidence. |

## 3. Backend Stage Model

Use the following backend stages for planning and reporting. They do not replace
G0-G10 gates; each stage maps to one or more gates.

| Stage | Name | Product target | Gate mapping | Exit state |
| --- | --- | --- | --- | --- |
| B0 | Latest-main backend readiness refresh | Current source, 211 source, deploy composition, runtime labels, backend containers, and readiness tools agree on the current backend subject. | G0-G1, S2-0 | `211 verified` only after fresh runtime/readiness evidence; otherwise `local partial`. |
| B1 | Memory and context usable | Session/project memory, context packs, provenance, retention, opt-out, delete/redaction, and export are usable behind policy. | G6, G9 | Memory can be enabled for selected workflows without private payload leakage. |
| B2 | Real sandbox usable | Docker-provider or equivalent sandbox can run governed SDK skill tasks with leases, quotas, egress policy, cleanup, and callback validation. | G7, G6, G9 | `fake` is no longer the only runnable provider for 211 skill/runtime smoke. |
| B3 | Worker and model-gateway capacity | Worker count/concurrency can increase under tenant-aware admission, queue bounds, model-gateway concurrency, timeout/backpressure, cost, and rollback evidence. | G5, G8, G9 | Target profile such as 10 concurrent sessions x peak 4 SDK subagents has recorded evidence before default increase. |
| B4 | Skills management and release governance | Skills can be uploaded, versioned, reviewed, released, rolled back, pinned, dependency-reviewed, and audited. | G6, G9 | Production Skill release gate has reviewed SBOM/license/vulnerability/release evidence and 211 acceptance. |
| B5 | Files, artifacts, and tool permission governance | File upload, artifact ACL, preview/download, exact tool approvals, MCP/shell/filesystem policy, and audit deny paths are complete. | G6, G7, G9 | Ordinary users can run governed file tasks without cross-user/tenant leakage or broad allow replay. |
| B6 | Operations beta and department workflow readiness | Admin Runtime, trace/export, release evidence runtime export, alerting, golden-set eval, and 1-2 workflow owners are ready. | G9, G10 | Department workflow beta can start with documented capacity, quality, audit, rollback, and support model. |

### 3.1 Stage Evidence Matrix

Each backend stage must report the narrowest true status. Docs-only changes can
clarify the target, but they do not upgrade runtime status.

| Stage | Minimum issue/PR chain | Local evidence | 211/runtime evidence | Gate closable only when |
| --- | --- | --- | --- | --- |
| B0 | One S2-0 refresh issue and PR per latest-main runtime subject. | Readiness JSON/markdown, source-authority tests, source-runtime relation checks, and docs consistency tests. | API/worker/source markers, repo-local compose labels, container image labels, runtime subject, source revision, health, and current-subject Foundation Runtime concurrency evidence. | Readiness has no current-source stage blocker and runtime evidence is redacted, reviewed, and tied to the merged source. |
| B1 | One memory/context issue per selected workflow slice. | Scope, opt-out, retention, delete/redaction, export, context-pack, and deny-path tests. | One memory-enabled 211 workflow smoke with provenance and no private projection leaks. | Memory is enabled only for governed scopes and long-term memory remains policy-bound. |
| B2 | One sandbox provider issue plus one hardening evidence issue if needed. | Lease lifecycle, ownership, callback, cleanup, policy, and projection tests. | Docker/equivalent provider launch, command, artifact, cancel, cleanup, orphan scan, and redacted evidence on 211. | `fake` is local/test-only and the real provider has quota, egress, security option, cleanup, and rollback evidence. |
| B3 | One capacity-profile issue plus evidence PRs for each load profile. | Bounded load harness, model-gateway policy tests, queue/admission tests, and failure-mode tests. | Approved 211 bounded load or explicitly reduced smoke covering the seven evidence gates and SDK subagent fanout pressure. | Operator-reviewed evidence proves the selected profile before raising defaults. |
| B4 | One Skill lifecycle issue plus release-governance PRs. | Upload validation, version immutability, release transitions, rollback, dependency metadata, pinning, and deny-path tests. | One reviewed Skill run through queue/worker/sandbox with used-skill evidence and artifacts on 211. | Skill releases have reviewed SBOM/license/vulnerability/dependency evidence and immutable run snapshots. |
| B5 | One file/artifact/tool-permission issue per high-risk workflow family. | File namespace, artifact ACL, preview/download, exact decision binding, replay denial, and cross-tenant deny tests. | File upload -> governed run -> artifact preview/download -> unauthorized deny smoke on 211. | Ordinary-user file workflows cannot bypass backend ACL, exact tool permission, or redaction controls. |
| B6 | One operations-beta issue per dashboard/export/alert/workflow package. | Admin Runtime, trace/export, alert policy, golden-set eval, rollback, and ordinary-user deny tests. | Admin Runtime smoke, trace/export smoke, alert calibration, workflow smoke, and rollback drill on 211. | Named workflow owners accept capacity, quality, audit, cost, support, and rollback evidence. |

## 4. Gate And Acceptance Boundaries

| Gate | Backend boundary | What can be claimed | What cannot be claimed |
| --- | --- | --- | --- |
| G0-G1 Source Authority / Security | Source, runtime labels, deploy composition, trusted auth, RBAC, tenant isolation, redaction, and backend/frontend projection boundaries match. | Current backend subject is source-authority aligned when readiness and 211 evidence agree. | Production readiness or department rollout from source checks alone. |
| G2-G4 Control Plane Contracts | Schema, repositories, routes, indexes, migrations, and public/admin projection shapes are stable. | Shared backend contracts are regression-covered for touched modules. | Runtime capacity, sandbox hardening, or Skill release closure. |
| G5 Run Lifecycle / Worker Runtime | Queue lease, heartbeat, retry, cancel, resume, checkpoint, dead letter, idempotency, tenant-aware admission, and bounded metadata are operational. | Controlled workload execution under current defaults. | Raising worker defaults without recorded load evidence. |
| G6 Tool / Skill / Memory Governance | Tool permission, Skill versions/snapshots, memory/context policy, artifact/file projections, and admin policy are enforceable and auditable. | Governed selected workflows can run under fail-closed controls. | Broad write-tool, global memory, or production Skill release before evidence. |
| G7 Sandbox / Resource Hardening | Real sandbox provider has egress policy, quota, cleanup, security options, leases, callback token, and orphan prevention. | Docker/equivalent sandbox is usable for approved skill/runtime smoke after 211 evidence. | Treating fake provider as production or exposing unrestricted Docker socket. |
| G8 Multi-Agent Controlled Beta | SDK Agent/subagent fanout is governed inside one platform run; capacity/cost/event/artifact pressure is measured. | Selected SDK subagent workflows can be enabled after load and governance evidence. | A platform-owned parent/child orchestration product or ordinary-user broad exposure. |
| G9 Observability / Quality / Ops | Admin Runtime, latency/error/token/cost, model-gateway pressure, trace/export, release evidence, alerts, and quality checks are operational. | Operators can diagnose and approve controlled beta operation. | Department rollout without SLO, alert, rollback, and workflow evidence. |
| G10 Internal Beta / Department Rollout | 1-2 real workflows have owners, capacity profile, quality gates, cost budget, audit, rollback, and support path. | Department workflow beta can begin for named workflows. | General availability or unbounded user expansion. |

Status labels must remain precise:

- `local partial`: local code/docs/tests only.
- `PR ready`: scoped verification passed and review boundary is documented.
- `merged`: PR is merged to main.
- `211 verified`: relevant 211 runtime/source/smoke evidence exists.
- `gate closable`: all gate evidence, review, deployment, and issue closure
  criteria are satisfied.

Gate closure rules:

1. `local partial` becomes `PR ready` only after scoped tests and the review
   boundary are recorded in the PR.
2. `PR ready` becomes `merged` only after the PR is merged to main.
3. Runtime-affecting backend work becomes `211 verified` only after 211 source,
   image labels, containers, health, smoke, and redacted evidence match the
   merged source.
4. A stage becomes `gate closable` only after the issue is closed with linked
   PR, review, verification, and runtime evidence where required.
5. Docs-only PRs may align the roadmap and acceptance wording, but they cannot
   close B0-B6 runtime gates.

## 5. Stage Requirements

### 5.1 B0 Latest-Main Backend Readiness Refresh

Goal: prevent historical S1 evidence from being mistaken for latest-main backend
closure.

Required backend evidence:

- `tools/foundation_alpha_readiness.py --format json` result for current source.
- Source-authority check covering local source, 211 source, repo-local deploy
  composition, image labels, and backend/worker containers.
- Backend API/worker smoke on 211 when runtime-affecting changes exist.
- Redacted release evidence entry with exact source subject.

Acceptance boundary:

- If readiness reports `runtime_rollout_required`,
  `foundation_alpha_stage_complete=false`, stale runtime labels, or missing
  Foundation Runtime concurrency evidence, the state is not current-source S1
  complete.
- Docs-only updates may be `local partial` or `merged`; they are not
  `211 verified`.

### 5.2 B1 Memory And Context Usable

Goal: make memory useful for real document/workflow tasks without violating
tenant or user policy.

Backend requirements:

- Memory scopes: session, project/workspace, and policy-gated long-term memory.
- Opt-out and disablement prevent both reads and writes in the affected scope.
- Retention cleanup, delete/redaction, export readiness, and admin inventory.
- Context pack persistence with version, generated-at timestamp, provenance,
  selected messages/files/memory items, and snapshot binding to runs.
- Public/admin projections expose provenance and status, not raw private memory.

Acceptance tests and evidence:

- Focused tests for tenant/workspace/user/session scoping.
- Deny-path tests for cross-tenant and cross-user memory access.
- Tests proving opt-out blocks reads and writes.
- Delete/redaction tests proving removed memory does not reappear in context.
- 211 smoke for one selected memory-enabled document workflow before claiming
  `211 verified`.

Not accepted:

- Enabling long-term memory globally by default.
- Storing raw executor logs or private payloads as user-visible memory.
- Treating frontend display state as canonical context selection.

### 5.3 B2 Real Sandbox Usable

Goal: make sandbox execution operational for governed SDK skill tasks.

Backend requirements:

- Docker-provider or equivalent runtime selected through platform policy, not
  user payload.
- Sandbox lease lifecycle: create, renew, release, cancel, cleanup, orphan scan.
- Workspace binding to tenant/workspace/session/run.
- Resource policy: CPU, memory, disk, process, timeout, and artifact output
  bounds.
- Network/egress policy and deny-by-default behavior for risky workflows.
- Callback token or equivalent mechanism for trusted sandbox event/artifact
  submission.
- Security options and Docker socket exposure policy documented and verified.

Acceptance tests and evidence:

- Unit/integration tests for lease ownership and cleanup.
- 211 Docker-capable smoke for sandbox launch, command execution, artifact
  return, cancel, cleanup, and orphan prevention.
- Public/admin projection tests proving workdirs, command fingerprints, and raw
  runtime paths are not exposed.
- Redacted release evidence with provider, image/source subject, limits, and
  cleanup result.

Not accepted:

- `SANDBOX_CONTAINER_PROVIDER=fake` as production proof.
- Manual Docker exec evidence without platform lease and audit records.
- Unrestricted Docker socket mount as the default trust boundary.

### 5.4 B3 Worker And Model-Gateway Capacity

Goal: increase worker capacity only when the system can survive expected
concurrency.

Initial target profile:

- 10 concurrent user sessions.
- Peak 4 SDK subagents per session for selected workflows.
- Production defaults remain unchanged until evidence says otherwise.

Backend requirements:

- Tenant-aware and user-aware queue admission.
- Bounded queue metadata and indexed lookups under load.
- Worker heartbeat, maintenance, lease timeout, retry, cancel, resume, and dead
  letter handling.
- Model-gateway concurrency limit, request timeout, retry/backoff,
  per-provider policy, and backpressure projection.
- Token/cost ledger by tenant/user/session/run/model.
- Artifact/event write volume checks and cleanup pressure checks.
- Operator-selectable capacity profiles with rollback path.

Seven required evidence gates:

1. API burst.
2. Run creation burst.
3. Queue depth and lease latency.
4. Worker start and heartbeat stability.
5. Model-gateway timeout/backpressure.
6. Sandbox/container pressure.
7. Cleanup, dead-letter, and artifact/event volume.

Acceptance tests and evidence:

- Local bounded-load harness result for the target profile.
- 211 bounded-load or approved reduced smoke with explicit scale limits.
- Admin Runtime projection showing limits, active load, backpressure, and
  reason for rejecting additional work.
- Failure-mode evidence: model timeout, queue saturation, sandbox launch
  failure, cancel, cleanup, and retry.

Not accepted:

- Raising `QUEUE_*`, worker count, model-gateway concurrency, or SDK turns by
  config only.
- Reporting a successful single run as capacity evidence.
- Ignoring model provider limits or token/cost pressure.

### 5.5 B4 Skills Management And Release Governance

Goal: make Skills a governed backend product surface, not only files in an
image or upload directory.

Backend requirements:

- Skill upload/import with validation of `SKILL.md`, supporting files, binary
  policy, size limits, and dependency metadata.
- Skill versioning, immutable content hash, release state, deprecation, rollback,
  and owner/reviewer fields.
- Pinned `run_skill_snapshots` for every run.
- Used-skill evidence in run events and release evidence.
- Dependency/SBOM/license/vulnerability review state.
- Tenant/workspace/role visibility and execution policy.
- Admin release dashboard APIs with deny-by-default production behavior for
  unreviewed Skills.

Acceptance tests and evidence:

- Tests for upload validation, version immutability, release transitions, and
  rollback.
- Tests for pin mismatch rejection and used-skill recording.
- Deny-path tests for unreviewed, disabled, cross-tenant, or unauthorized Skill
  execution.
- 211 smoke running at least one reviewed Skill through the queue/worker/sandbox
  path and collecting artifacts.

Not accepted:

- Mutable global skill folders as production authority.
- Running a Skill from local filesystem state without snapshot pinning.
- Production release without reviewed dependency/license/vulnerability evidence.

### 5.6 B5 Files, Artifacts, And Tool Permission Governance

Goal: close the risk created by real file tasks, writable tools, MCP, shell, and
artifact outputs.

Backend requirements:

- File upload namespace by tenant/workspace/user/session/run.
- Artifact provenance, ACL, preview allowlist, download authorization, retention,
  and redaction.
- Exact tool permission requests with `tool_call_id` or stable request
  fingerprint.
- Allow/ask/deny taxonomy for shell, network, MCP, filesystem, and high-risk
  actions.
- Admin policy/history APIs and ordinary-user permission cards.
- Replay protection: `allow_once` cannot authorize unrelated calls.

Acceptance tests and evidence:

- Cross-tenant and cross-user file/artifact deny tests.
- Artifact preview URL safety tests.
- Tool permission tests for exact decision binding and replay denial.
- 211 smoke for file upload -> Skill run -> artifact preview/download -> deny
  unauthorized download.

Not accepted:

- Broad "latest allow for this tool" lookup.
- Artifact URLs exposing raw storage keys or runtime paths.
- Frontend or executor bypass of backend ACL.

### 5.7 B6 Operations Beta And Department Workflow Readiness

Goal: make the backend operable by admins before department rollout.

Backend requirements:

- Admin Runtime dashboard APIs for queue, run, worker, sandbox, model gateway,
  token/cost, latency, errors, dead letters, and release evidence.
- Trace/audit export and release-evidence runtime export.
- Alert delivery and calibration for stuck runs, queue saturation, sandbox
  failures, model-gateway pressure, and artifact errors.
- Golden-set eval runtime for selected workflows.
- Workflow owner, capacity profile, cost budget, quality gate, rollback path,
  and support ownership for each beta workflow.

Acceptance tests and evidence:

- Admin Runtime 211 smoke with same-tenant admin success and ordinary-user deny.
- Trace/export smoke with redaction.
- Alert calibration evidence.
- Workflow-specific 211 smoke and rollback drill.

Not accepted:

- Department rollout based only on a successful POC run.
- Admin dashboards that expose secrets, raw queue payloads, sandbox workdirs, or
  executor-private payloads.

## 6. Reference Code Projects

External code projects are references, not product authority. Any imported code
requires source pinning, license/provenance review, and an adaptation plan.

| Project | Reference area | Absorb | Do not absorb |
| --- | --- | --- | --- |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Memory, checkpoints, durable agent state | Short-term checkpoint and long-term store concepts; separation of run state and cross-run memory. | LangGraph as the platform runtime or memory policy authority. |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Agent runtime and Docker sandbox patterns | Docker workspace/sandbox lifecycle ideas, event visibility, safety warnings around running without sandbox. | OpenHands agent server, filesystem trust model, or Docker socket exposure as ai-platform default. |
| [E2B](https://github.com/e2b-dev/e2b) / [E2B Code Interpreter](https://github.com/e2b-dev/code-interpreter) | Sandbox/code interpreter API | Sandbox template, command/code execution API ergonomics, artifact return concepts. | External SaaS dependency, API-key-controlled sandbox authority, or replacement for platform lease/audit. |
| [Daytona](https://github.com/daytonaio/daytona) | Elastic sandbox infrastructure | Fast sandbox lifecycle and OCI/Docker-compatible sandbox concepts. | Daytona as required infrastructure before ai-platform can run internally. |
| [Temporal Python SDK](https://github.com/temporalio/sdk-python) | Durable workflows, retries, long-running orchestration | Workflow/activity separation, retry semantics, signal/query/update ideas for future durable operations. | Replacing current queue/worker path without a migration issue and evidence plan. |
| [Celery](https://github.com/celery/celery) | Distributed Python task queues | Worker/broker vocabulary, horizontal scaling, scheduling, retry and monitoring patterns. | Dropping Celery in as an unplanned replacement for existing queue contracts. |
| [LiteLLM](https://docs.litellm.ai/docs/proxy/users) | Model gateway, budgets, rate limits, spend tracking | Per-user/team/key budget/rate-limit/spend-tracking concepts and provider abstraction ideas. | Outsourcing ai-platform provider secrets, cost authority, or tenant policy to an external proxy without controls. |
| [OpenFGA](https://github.com/openfga/openfga) | Fine-grained authorization and relationship-based ACLs | Relationship model ideas for tenant/workspace/user/run/file/artifact/skill permission graphs and deny-path test matrices. | Replacing ai-platform RBAC, tenant isolation, audit, or source authority without a migration gate. |
| [Open Policy Agent](https://github.com/open-policy-agent/opa) | Policy-as-code and decision logging | Policy bundle and test patterns for sandbox, tool, egress, dependency, and release decisions. | A side policy engine that can override backend fail-closed checks or hide decisions from audit. |
| [Langfuse](https://github.com/langfuse/langfuse) | LLM observability, traces, evals, token/cost tracking | Trace/span vocabulary, model usage/cost views, prompt/eval observability concepts. | Sending private prompts/files to an external observability store or replacing ai-platform release evidence. |
| [OpenTelemetry Collector](https://github.com/open-telemetry/opentelemetry-collector) | Telemetry pipeline | Metrics/traces/logs collection and export patterns for Admin Runtime and operations evidence. | Treating telemetry export as product acceptance without ai-platform redaction, RBAC, and release-evidence review. |
| [Backstage](https://github.com/backstage/backstage) | Internal developer portal, catalog, templates | Catalog ownership, lifecycle metadata, approval workflow, and template/release UX ideas for Skills market/admin views. | Backstage as the user-facing AI platform shell or authority over Skill execution policy. |
| [Dify](https://github.com/langgenius/dify) | Skill/workflow marketplace and governance inspiration | Workflow/agent app management, knowledge/workflow governance, model management, observability ideas. | Dify backend, workflow engine, tenant model, or no-code product model as ai-platform authority. |

Reference priority for implementation:

1. For memory/context, study LangGraph persistence concepts first.
2. For sandbox, compare OpenHands, E2B, and Daytona, but implement ai-platform
   lease/audit policy first.
3. For worker capacity, study Temporal/Celery patterns, but keep current
   ai-platform queue contracts unless a future migration issue proves otherwise.
4. For model gateway/cost, study LiteLLM budgets/rate limits/spend tracking.
5. For authorization and policy, study OpenFGA and OPA, but keep ai-platform as
   the enforcement and audit source of truth.
6. For observability, study Langfuse and OpenTelemetry, but keep release
   evidence, redaction, and admin projections platform-owned.
7. For Skills management, study Dify, Backstage, and LibreChat-style Skills UX
   only as workflow/release inspiration; backend authority remains ai-platform.

## 7. Backend PRD Definition Of Done

This backend productization program is done only when:

1. B0-B6 each have linked issues, implementation PRs, review evidence, targeted
   tests, and 211 evidence where runtime is involved.
2. Memory can be enabled for selected workflows with opt-out, retention,
   delete/redaction, export, provenance, and deny-path evidence.
3. Real sandbox provider can run governed SDK skill tasks on 211 with cleanup
   and no private projection leaks.
4. Worker/model-gateway defaults are raised only after seven-gate load evidence
   proves the selected target profile.
5. Skills have upload/version/release/rollback/dependency policy and pinned
   snapshot execution evidence.
6. File/artifact/tool-permission deny paths are covered locally and in 211 smoke
   for runtime paths.
7. Admin Runtime gives operators enough live visibility to diagnose queue,
   worker, sandbox, model gateway, cost, errors, and release evidence.
8. One or two department beta workflows have owner signoff, capacity profile,
   cost budget, quality checks, rollback path, and 211 acceptance.

## 8. Immediate Implementation Order

1. B0 latest-main backend readiness/source-authority refresh.
2. B1 memory/context usable for one selected document workflow.
3. B2 real sandbox provider smoke for governed SDK skill execution.
4. B3 bounded load harness for 10 sessions x peak 4 SDK subagents, initially
   measured without raising defaults.
5. B4 Skills management release-governance slice.
6. B5 file/artifact/tool-permission deny-path hardening.
7. B6 Admin Runtime runtime acceptance and one workflow beta package.

Do not mark any later stage `gate closable` because an earlier local test passed.
Every stage must report the narrowest true status label.
