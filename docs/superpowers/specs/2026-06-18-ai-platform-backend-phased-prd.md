# AI Platform Backend Productization PRD

> Status: active backend companion PRD for S2 backend productization.
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

### 0.1 Current Evidence Snapshot

This PRD is written against the latest local source snapshot observed while
editing this document:

| Source | Observed result |
| --- | --- |
| `python tools/foundation_alpha_readiness.py --format json` | `foundation_alpha_stage_complete=false`, `foundation_alpha_stage_status=runtime_rollout_required`, blocker `foundation_runtime_concurrency_evidence`, source tree `f8a0f3c1168c34663850345d8f30358d435a0134`, runtime subject `569887369e0358f08a408c473395521b22c8e0a7`, runtime relation `source_synced_runtime_pending`, and runtime-affecting changes since runtime subject include `app/b2_sandbox_readiness.py`, `app/release_evidence_export_acceptance.py`, `app/release_evidence_readiness.py`, `app/routes/runs.py`, `tools/b1_memory_context_readiness.py`, `tools/b2_sandbox_readiness.py`, and `tools/verify_b1_memory_context_workflow.py`. |
| `python tools/b1_memory_context_readiness.py --format json` | `status=runtime_acceptance_recorded`, `status_label=local partial`, runtime smoke status label can be `211 verified` only for the recorded smoke scope, closed gate-boundary gaps include `b1_memory_export_boundary` and `b1_rollback_boundary`; open gaps remain `b1_issue_review_and_closure_evidence` and `b1_runtime_evidence_review_against_merged_source` because the reviewed smoke is tied to runtime subject `52ac62cfbbab47172a659dda11e41aa4b2a5d699` while current source is `f8a0f3c1168c34663850345d8f30358d435a0134`. |
| `python tools/b2_sandbox_readiness.py --format json` | `status=local_contract_ready_runtime_smoke_required`, `status_label=local partial`, runtime acceptance status `missing_211_real_sandbox_smoke`; a raw smoke without reviewed evidence stays `local partial`; open gaps remain `b2_211_real_sandbox_smoke`, `b2_reviewed_release_evidence`, and `b2_issue_review_and_closure_evidence`. |
| `python tools/governance_readiness.py --format json` | `status=partial_blocked`, `sandbox_provider=fake`, G6 open gaps include skill dependency runtime acceptance, Admin Skill dashboard visual/211 acceptance, B1 closure evidence, and frontend projection gaps. |

The snapshot is not a permanent source of truth. It prevents this document from
overstating the current state: S1 historical evidence exists, but latest-main
runtime closure is still blocked until the named evidence is refreshed and
reviewed.

### 0.2 Status Transition Contract

All backend work must use the same status language in issues, PRs, readiness
output, release evidence, and user-facing reports.

| Status | Meaning | Minimum evidence | Explicitly not enough |
| --- | --- | --- | --- |
| `local partial` | Local source, docs, tests, or static evidence exists, but no merge/runtime claim is made. | Local targeted tests or readiness output for the changed scope. | Runtime screenshots, historical evidence, or a successful run from an older source. |
| `PR ready` | A scoped PR can be reviewed. | Issue link, branch, PR body, changed-scope verification, and documented boundaries. | A local commit without PR review surface or an unverified generated artifact. |
| `reviewed` | Independent review happened and was recorded. | Review comment, requested-change resolution, or explicit reviewer approval/evidence comment. | Self-review, subagent summary, or passing tests alone. |
| `merged` | The PR has landed on `main`. | Merge commit or GitHub PR merged state. | A pushed branch or local branch named `main`. |
| `211 verified` | Runtime evidence exists for the merged source on 211 or another named Docker-capable target. | Source/runtime relation, image labels, container health, smoke output, redaction scan, and reviewed release evidence. | Local tests, docs-only changes, or fake-provider evidence. |
| `gate closable` | The stage or gate can be closed. | Linked issue closure with PR, review, verification, runtime evidence where required, and residual caveats. | Any single passing smoke, any docs-only PR, or historical S1 baseline evidence. |

## 1. Product Decision

The backend route after S1 is productization of the ai-platform control plane,
not a new agent harness and not a second backend. Claude Agent SDK remains the
execution layer. ai-platform owns identity, tenancy, run lifecycle, queue,
sandbox policy, memory, skills, tools, files, artifacts, events, audit, model
gateway limits, cost, and release evidence.

The project is past the POC proving phase. Productization means turning the
accepted Foundation Alpha baseline into repeatable, supportable backend
capabilities. Every capability must have a product owner-facing contract,
operator evidence, rollback boundary, and issue/PR/review path before it is
reported as more than `local partial`.

The first backend productization wave focuses on four P0 capabilities:

| Priority | Capability | Product boundary | Primary gates |
| --- | --- | --- | --- |
| P0-1 Memory/context usable | Memory/context is usable under tenant, session, retention, redaction, and delete/export policy. | Enable only selected governed workflows until deny paths, provenance, export, rollback, and long-term memory policy are evidenced. | G6, G9 |
| P0-2 Real sandbox usable | Real sandbox execution is usable instead of treating `fake` as production. | Real provider evidence must include lease, callback, quota, egress, cleanup, cancel, artifact return, and projection redaction. | G7, G6, G9 |
| P0-3 Worker/model-gateway capacity evidence | Worker capacity can be increased only with queue, model-gateway, sandbox-pressure, token/cost, and backpressure evidence. | Initial target is 10 concurrent sessions with peak 4 SDK subagents per session; defaults stay unchanged until the selected profile is proven. | G5, G8, G9 |
| P0-4 Skills management and release governance | Skills management is usable for upload, versioning, release, rollback, dependency review, permission scope, and pinned run snapshots. | Mutable folders are not production authority; reviewed versions, dependencies, release state, and used-skill evidence are required. | G6, G9 |

The supporting backend capabilities are model-gateway/cost controls,
artifact/file governance, exact tool permission policy, and Admin Runtime
observability. Without these, the four P0 capabilities cannot be safely exposed.
These supporting capabilities are not optional stretch work:

- Model-gateway and cost controls are required before B3 can raise worker or
  SDK subagent fanout capacity.
- File/artifact governance and exact tool permission policy are required before
  B4/B5 can expose real document, shell, MCP, or writable-tool workflows.
- Admin Runtime observability and release-evidence export are required before
  B6 can become an operations beta instead of a one-off POC.
- Source authority and auth/RBAC/tenant isolation remain the foundation under
  every stage; when they drift, return to B0 before claiming later-stage
  progress.

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

### 3.2 Stage Entry And Exit Gates

Each stage must have an explicit entry and exit gate before it is reported
outside the engineering thread. Entry means the slice is allowed to start; exit
means it can move to the next status label. These gates are intentionally
stricter than a task checklist because they protect against claiming product
progress from local-only evidence.

| Gate | Required condition |
| --- | --- |
| B0 entry | Latest `main` is identified; the current runtime subject, 211 source path, repo-local deploy composition, backend/worker containers, and readiness command are known; an issue exists for the refresh if runtime evidence is required. |
| B0 exit | Readiness has no current-source stage blocker, current-subject Foundation Runtime concurrency evidence exists, runtime labels and source relation are redacted and reviewed, and remaining caveats are named as non-closing follow-ups. |
| B1 entry | A concrete workflow is selected; the memory scope, policy, retention, opt-out, delete/redaction, export, and context-pack contract are named; long-term memory default remains fail-closed. |
| B1 exit | Focused deny-path tests pass; one reviewed 211 memory-enabled workflow smoke for the merged source proves context provenance and no private projection leakage; the enabled scope, retention, opt-out, export, and rollback boundaries are recorded. |
| B2 entry | A real sandbox provider or equivalent isolation path is selected; lease, callback, quota, egress, cleanup, security-option, and artifact boundaries are designed before runtime exposure. |
| B2 exit | 211 Docker/equivalent smoke proves launch, command execution, artifact return, cancel, cleanup, orphan scan, and projection redaction; `fake` remains local/test-only. |
| B3 entry | Target capacity profile is named, starting with 10 concurrent sessions and peak 4 SDK subagents per session for selected workflows; current defaults stay unchanged. |
| B3 exit | Seven-gate capacity evidence is operator-reviewed, model-gateway timeout/backpressure and token/cost accounting are recorded, rollback is documented, and only the proven profile can be enabled. |
| B4 entry | Skill lifecycle state model is agreed: upload/import, immutable version, release review, deprecation, rollback, dependency metadata, visibility, and pinned run snapshots. |
| B4 exit | Reviewed Skill smoke on 211 records used-skill evidence and artifacts; SBOM/license/vulnerability/dependency review evidence is attached; unreviewed, disabled, cross-tenant, and unauthorized Skill paths fail closed. |
| B5 entry | A high-risk file/artifact/tool workflow family is selected; namespace, ACL, exact tool decision, replay, preview/download, retention, and redaction rules are specified. |
| B5 exit | 211 smoke proves file upload -> governed run -> artifact preview/download -> unauthorized deny; exact tool decisions bind to `tool_call_id` or stable request fingerprint. |
| B6 entry | One operations package or department workflow package has owner, SLO, capacity profile, cost budget, quality gate, alert, trace/export, rollback, and support requirements. |
| B6 exit | Admin Runtime, trace/export, alert calibration, golden-set evaluation, workflow smoke, rollback drill, and owner signoff are recorded with redacted release evidence. |

### 3.3 Universal Blocking Conditions

Any of the following conditions blocks a stage exit, even if some local tests or
one happy-path smoke passed:

- No stage can exit while its linked issue remains open without an evidence comment
  that explains why the issue is intentionally left open.
- Docs-only PRs may align the roadmap and acceptance wording, but they cannot
  create `211 verified` or `gate closable` status.
- No capacity or SDK subagent fanout default can increase from configuration alone.
  B3 requires recorded load evidence and rollback.
- No sandbox claim can use `fake` provider evidence for production acceptance.
  B2/G7 requires Docker/equivalent provider evidence on a Docker-capable host.
- Long-term memory cannot become a default until opt-out, retention, delete,
  redaction, export, tenant deny paths, and rollback are proven for that scope.
- Mutable filesystem Skill folders cannot be production authority. B4 requires
  immutable versions, content hashes, release state, and pinned run snapshots.
- Broad "latest allow" tool permission lookup cannot authorize high-risk tools.
  B5 requires exact request binding and replay denial.
- Public or admin projections cannot expose raw storage keys, sandbox workdirs,
  executor-private payloads, command fingerprints, secrets, callback tokens, or
  real `.env` values.
- Runtime-affecting source cannot be called current if 211 source, image labels,
  containers, health, smoke, and release evidence do not match the merged source.

### 3.4 Stage Deliverables

Use these deliverables to decide whether a stage has produced a reviewable PR
chain. A deliverable is not automatically runtime evidence; it becomes
`211 verified` only when the required runtime/smoke evidence exists.

| Stage | Required deliverables |
| --- | --- |
| B0 | Latest-main readiness report; source/runtime relation evidence; 211 source and image-label smoke; reviewed release evidence caveats. |
| B1 | Memory policy and context-pack contracts; memory workflow verifier; reviewed current-source 211 smoke evidence; rollback/export notes. |
| B2 | Real sandbox provider profile; lease/callback/egress/cleanup tests; 211 sandbox smoke evidence. |
| B3 | Capacity profile definition; bounded-load harness; seven-gate 211 evidence; Admin Runtime backpressure projection. |
| B4 | Skill upload/version/release/rollback contracts; dependency evidence contract; reviewed skill-run smoke. |
| B5 | File/artifact namespace and ACL contracts; exact tool-permission replay tests; file-to-artifact 211 smoke. |
| B6 | Admin Runtime operations package; trace/export and alert evidence; workflow owner signoff; rollback drill evidence. |

### 3.5 Gate Closure Checklist

Use this checklist before moving any backend stage from `merged` or
`211 verified` to `gate closable`.

| Stage | Issue evidence | PR evidence | Review evidence | Required local verification | Required 211 evidence | Closure blocker if missing |
| --- | --- | --- | --- | --- | --- | --- |
| B0 | S2-0/latest-main source-authority issue links the target source, runtime subject, and known caveats. | PR updates source/readiness/runtime evidence without unrelated feature expansion. | Review confirms no stale source/runtime wording and no historical-S1 overclaim. | `tools/foundation_alpha_readiness.py --format json`, source-authority/doc tests, release-evidence export preflight if evidence changes. | API/worker labels, source marker, health, current-subject runtime/concurrency evidence, redacted release evidence. | Any `runtime_rollout_required`, stale label, or unmatched runtime-affecting source path. |
| B1 | Memory/context issue names the selected workflow, policy scope, opt-out/export/delete/redaction behavior, and whether it remains open. | PR changes only memory/context contracts, verifier, or evidence for that slice. | Review confirms long-term memory remains fail-closed and public projections hide private material. | Memory erasure tests, context-pack tests, B1 readiness, route/projection deny-path tests. | `tools/verify_b1_memory_context_workflow.py` evidence from 211 for merged source. | Open `b1_issue_review_and_closure_evidence`, stale or open merged-source runtime review, or unproven export/rollback boundary. |
| B2 | Sandbox issue names provider, limits, egress, callback, cleanup, and rollback assumptions. | PR separates fake/local contracts from real-provider runtime path. | Review confirms no Docker socket trust expansion and no user-payload provider override. | Lease lifecycle, callback, cleanup, cancel, projection redaction, and fail-closed provider tests. | Docker/equivalent launch, command, artifact, cancel, cleanup, orphan scan, and redacted evidence. | `SANDBOX_CONTAINER_PROVIDER=fake` is the only evidence, or cleanup/orphan proof is absent. |
| B3 | Capacity issue names the profile, starting with 10 sessions x peak 4 SDK subagents, and states defaults remain unchanged. | PR adds harness, limits, metrics, or evidence without silently raising defaults. | Review confirms provider limits, token/cost, and rollback are covered. | Bounded load harness, queue/admission tests, model-gateway timeout/backpressure tests, failure-mode tests. | Seven-gate bounded-load or approved reduced 211 smoke with scale limits and Admin Runtime projection. | Any missing seven-gate evidence item, missing rollback, or config-only capacity increase. |
| B4 | Skill lifecycle issue names upload/version/release/rollback/dependency evidence and release authority. | PR keeps mutable folders out of production authority and pins run snapshots. | Review confirms SBOM/license/vulnerability/dependency evidence boundaries. | Admin Skill route tests, release transition tests, version immutability tests, pin mismatch/used-skill tests, unauthorized Skill deny tests. | Reviewed Skill run through queue/worker/sandbox on 211 with used-skill evidence and artifacts. | Missing reviewed dependency evidence, unreviewed Skill execution, or mutable global folder authority. |
| B5 | File/artifact/tool issue names workflow family, namespace, exact permission binding, retention, and redaction. | PR keeps backend ACL and exact tool decisions as authority. | Review confirms replay denial, cross-tenant deny, and projection redaction. | File namespace tests, artifact ACL/preview/download tests, exact permission lookup/replay tests, tool policy tests. | File upload -> governed run -> artifact preview/download -> unauthorized deny smoke. | Broad latest-allow decision lookup, raw storage key exposure, or frontend/executor ACL bypass. |
| B6 | Operations-beta issue names owner, workflow, SLO, cost budget, quality gate, alert, trace/export, support, and rollback. | PR updates Admin Runtime/observability/workflow evidence without expanding beta scope. | Review confirms redaction, owner acceptance, and rollback drill evidence. | Admin Runtime tests, trace/export tests, alert policy tests, golden-set eval tests, ordinary-user deny tests. | Admin Runtime smoke, trace/export smoke, alert calibration, workflow smoke, rollback drill, owner signoff. | Missing owner signoff, missing rollback drill, or any secret/private payload in operations projections. |

### 3.6 Negative Acceptance Matrix

These negative checks are part of the product requirement. A happy path cannot
close a stage unless the matching denial paths are also represented in tests or
runtime evidence.

| Area | Required deny paths |
| --- | --- |
| Tenant/workspace/user/session isolation | Same-tenant other-user read denial, cross-tenant read denial, wrong-run access denial, stale session/context access denial. |
| Memory/context | Disabled policy blocks reads and writes, deleted/redacted memory is excluded from future context, long-term memory remains unavailable by default, export omits deleted/expired records. |
| Sandbox | Unsupported provider fails closed, user cannot pick provider or workspace path, cancel does not release active leases before cleanup succeeds, orphan scan reports no leaked active container. |
| Worker/capacity | Queue saturation returns bounded backpressure, model timeout does not wedge the run, worker heartbeat loss creates recoverable state, dead-letter path is observable. |
| Skills | Disabled/unreviewed/cross-tenant Skill cannot be staged, version pin mismatch rejects execution, dependency policy violation blocks release, rollback requires a materializable snapshot. |
| Files/artifacts | Cross-user and cross-tenant preview/download fail, raw storage keys are not projected, expired/deleted artifacts are not downloadable. |
| Tool permissions | `allow_once` cannot be reused, wrong `tool_call_id` or fingerprint fails, high-risk/write tool requires current decision, unregistered MCP tool is denied. |
| Observability/export | Ordinary user cannot read admin runtime/export, trace/export omits private payloads, alert payloads contain categories and refs instead of secrets. |

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

B2 can now be tracked through `tools/b2_sandbox_readiness.py`, but that rollup
is source-level only. The B2 source contract is `local partial` until 211
Docker/equivalent evidence is generated, redacted, reviewed, and attached to
the linked issue. The current B2 rollup is deliberately narrow: it records the
provider profile, non-expansion invariants, required verifier scripts, and
remaining evidence gaps without claiming `211 verified` or `gate closable`.
The current 211 sandbox verifier currently enforces `admin_or_allowlist_only`
and `hardening.evidence_class` through the existing generated evidence shape.
B2/G7 still requires separate verifier/generator work before resource-limit,
egress-policy, security-option, and rollback-assumption evidence can be treated
as current verifier output or used in a `211 verified` claim.

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
requires source pinning, license/provenance review, and an adaptation plan. Do
not import a reference project's tenant, RBAC, memory, sandbox, or release
authority wholesale. Backend authority remains ai-platform.
No reference project is a dependency decision until an issue names the source,
license, imported files, adaptation boundary, and verification plan.

Reference use must follow this intake gate:

1. Create or reuse a GitHub issue that names the reference project, commit/tag,
   license, files or concepts to inspect, and the backend stage it supports.
2. Record why the reference is only an implementation reference and which
   ai-platform authority remains unchanged.
3. If code is copied or adapted, add license/provenance evidence and a minimal
   test proving the adapted behavior under ai-platform tenant/RBAC/audit rules.
4. If only concepts are borrowed, record the concept in the PR body and keep
   the codebase dependency-free.
5. Do not add a runtime dependency, side service, or hosted SaaS call without a
   separate architecture issue, security review, deployment plan, and rollback
   boundary.

| Project | Reference area | Absorb | Do not absorb |
| --- | --- | --- | --- |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Memory, checkpoints, durable agent state | Short-term checkpoint, long-running state, human-in-the-loop, and long-term store concepts; separation of run state and cross-run memory. | LangGraph as the platform runtime or memory policy authority. |
| [Mem0](https://github.com/mem0ai/mem0) | Agent memory product ergonomics | Memory extraction, user-visible memory management, and update/delete UX ideas for B1. | External memory service authority, unscoped cross-session memory, or private prompt/file ingestion outside ai-platform policy. |
| [Zep](https://github.com/getzep/zep) | Agent memory store and temporal knowledge | Session history, memory summarization, graph/temporal retrieval ideas, and memory provenance concepts. | Replacing ai-platform `memory_records`, context snapshots, tenant policy, retention, or deletion authority. |
| [Graphiti](https://github.com/getzep/graphiti) | Temporal knowledge graph memory | Episodic/temporal relationship extraction, conflict-aware memory updates, and provenance ideas for later B1 long-term memory research. | Long-term memory default enablement, cross-tenant graph authority, or replacing ai-platform memory delete/redaction policy. |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Agent runtime and Docker sandbox patterns | Docker workspace/sandbox lifecycle ideas, event visibility, safety warnings around running without sandbox. | OpenHands agent server, filesystem trust model, or Docker socket exposure as ai-platform default. |
| [E2B](https://github.com/e2b-dev/e2b) / [E2B Code Interpreter](https://github.com/e2b-dev/code-interpreter) | Sandbox/code interpreter API | Sandbox template, command/code execution API ergonomics, artifact return concepts. | External SaaS dependency, API-key-controlled sandbox authority, or replacement for platform lease/audit. |
| [Daytona](https://github.com/daytonaio/daytona) | Elastic sandbox infrastructure | Fast sandbox lifecycle and OCI/Docker-compatible sandbox concepts. | Daytona as required infrastructure before ai-platform can run internally. |
| [Temporal Python SDK](https://github.com/temporalio/sdk-python) | Durable workflows, retries, long-running orchestration | Workflow/activity separation, retry semantics, signal/query/update ideas for future durable operations. | Replacing current queue/worker path without a migration issue and evidence plan. |
| [Celery](https://github.com/celery/celery) | Distributed Python task queues | Worker/broker vocabulary, horizontal scaling, scheduling, retry and monitoring patterns. | Dropping Celery in as an unplanned replacement for existing queue contracts. |
| [Dramatiq](https://github.com/Bogdanp/dramatiq) / [Taskiq](https://github.com/taskiq-python/taskiq) | Python task queue alternatives | Lightweight worker, retry, broker, and async task ergonomics for B3 comparisons. | Replacing the current queue/worker path without proving migration value over targeted ai-platform hardening. |
| [LiteLLM](https://docs.litellm.ai/docs/proxy/users) | Model gateway, budgets, rate limits, spend tracking | Per-user/team/key budget/rate-limit/spend-tracking concepts and provider abstraction ideas. | Outsourcing ai-platform provider secrets, cost authority, or tenant policy to an external proxy without controls. |
| [Portkey](https://github.com/Portkey-AI/gateway) | AI gateway, routing, guardrails, observability | Gateway routing, rate-limit, fallback, request logging, and provider policy ideas for B3/G9. | Treating an external gateway as the source of tenant cost, model policy, or release evidence without ai-platform audit. |
| [OpenFGA](https://github.com/openfga/openfga) | Fine-grained authorization and relationship-based ACLs | Relationship model ideas for tenant/workspace/user/run/file/artifact/skill permission graphs and deny-path test matrices. | Replacing ai-platform RBAC, tenant isolation, audit, or source authority without a migration gate. |
| [SpiceDB](https://github.com/authzed/spicedb) | Zanzibar-style relationship authorization | Permission graph modeling, consistency vocabulary, and caveat/check patterns for B5 ACL design comparison. | External authorization authority for ai-platform tenant/run/file/artifact access without a migration gate. |
| [Open Policy Agent](https://github.com/open-policy-agent/opa) | Policy-as-code and decision logging | Policy bundle and test patterns for sandbox, tool, egress, dependency, and release decisions. | A side policy engine that can override backend fail-closed checks or hide decisions from audit. |
| [Keycloak](https://github.com/keycloak/keycloak) / [Authentik](https://github.com/goauthentik/authentik) / [Ory Kratos](https://github.com/ory/kratos) | Identity provider and company-login reference | OIDC/session, group/role mapping, admin login, and enterprise identity integration patterns for G0-G1 design checks. | Replacing ai-platform trusted principal projection, tenant mapping, or RBAC enforcement without an identity migration issue. |
| [Langfuse](https://github.com/langfuse/langfuse) | LLM observability, traces, evals, token/cost tracking | Trace/span vocabulary, model usage/cost views, prompt/eval observability concepts. | Sending private prompts/files to an external observability store or replacing ai-platform release evidence. |
| [Phoenix](https://github.com/Arize-ai/phoenix) | LLM tracing, evaluation, and debugging | Evaluation run, trace inspection, prompt/version analysis, and quality debugging ideas for B6. | Exporting private payloads or treating third-party eval output as gate closure without ai-platform redaction and review. |
| [OpenTelemetry Collector](https://github.com/open-telemetry/opentelemetry-collector) | Telemetry pipeline | Metrics/traces/logs collection and export patterns for Admin Runtime and operations evidence. | Treating telemetry export as product acceptance without ai-platform redaction, RBAC, and release-evidence review. |
| [promptfoo](https://github.com/promptfoo/promptfoo) / [Ragas](https://github.com/explodinggradients/ragas) / [Giskard](https://github.com/Giskard-AI/giskard) | LLM quality, regression, and golden-set evaluation | Eval-case format, regression matrix, red-team checks, RAG answer quality metrics, and evidence packaging ideas for B6. | Treating third-party eval scores as acceptance without workflow-owner thresholds, redaction, and ai-platform release evidence. |
| [MCP Gateway](https://github.com/IBM/mcp-context-forge) / [supergateway](https://github.com/supercorp-ai/supergateway) | MCP gateway and tool catalog routing | Server/tool catalog, gateway routing, transport bridging, and policy-surface ideas for B5 tool governance. | Tool execution authority, permission decisions, or raw MCP payload visibility outside ai-platform audit and exact-decision controls. |
| [Backstage](https://github.com/backstage/backstage) | Internal developer portal, catalog, templates | Catalog ownership, lifecycle metadata, approval workflow, and template/release UX ideas for Skills market/admin views. | Backstage as the user-facing AI platform shell or authority over Skill execution policy. |
| [Dify](https://github.com/langgenius/dify) | Skill/workflow marketplace and governance inspiration | Workflow/agent app management, knowledge/workflow governance, model management, observability ideas. | Dify backend, workflow engine, tenant model, or no-code product model as ai-platform authority. |
| [Open WebUI](https://github.com/open-webui/open-webui) | User-facing tool/function/plugin UX | Chat command ergonomics, function/tool management, knowledge UI, and admin-facing model/tool UX ideas that can inform frontend/backend projections. | Open WebUI backend, auth, model/provider authority, tool execution policy, or memory authority. |
| [LibreChat](https://github.com/danny-avila/LibreChat) | Chat shell, agents/tools UI, MCP-style UX | User interface patterns for Skills discovery, slash commands, run drawer, and tool configuration when the frontend companion PRD absorbs UI ideas. | Backend authority, model configuration truth, tenant/RBAC, or run lifecycle ownership. |
| [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm) | Workspace-oriented knowledge/task UX | Workspace, document knowledge, and task surface ideas for selected internal workflows. | Replacing ai-platform workspace/tenant isolation, memory policy, files, or artifacts. |

### 6.1 Reference Priority By Backend Stage

| Backend stage | Reference projects | What to study first |
| --- | --- | --- |
| B0 source/auth baseline | Keycloak, Authentik, Ory Kratos | OIDC/session, group/role mapping, admin login, and enterprise identity integration patterns. |
| B1 memory/context | LangGraph, Mem0, Zep, Graphiti | Memory/checkpoint model, memory UX, provenance, temporal memory, delete/update semantics. |
| B2 sandbox | OpenHands, E2B, Daytona | Sandbox lifecycle, workspace isolation, command execution, artifact return, cancellation ergonomics. |
| B3 capacity/model gateway | Temporal, Celery, Dramatiq, Taskiq, LiteLLM, Portkey | Durable retry vocabulary, worker scaling, provider limits, budgets, spend tracking, fallback/backpressure. |
| B4 Skills management | Backstage, Dify, Open WebUI, LibreChat, AnythingLLM | Catalog, release workflow, skill/app marketplace UX, slash/tool discovery patterns. |
| B5 authorization/files/tools | OpenFGA, SpiceDB, Open Policy Agent, MCP Gateway, supergateway | Relationship-based ACLs, policy bundles, gateway/tool catalog routing, decision logs, deny-path test matrices. |
| B6 observability/ops | Langfuse, Phoenix, OpenTelemetry Collector, promptfoo, Ragas, Giskard | Trace vocabulary, eval runs, token/cost views, metrics/traces/log export, quality regression, and redaction patterns. |

Reference priority for implementation:

1. For source/auth baseline, study Keycloak, Authentik, and Ory Kratos only for
   company-login/OIDC/session integration patterns; keep ai-platform tenant and
   RBAC enforcement authoritative.
2. For memory/context, study LangGraph persistence concepts first, then Mem0,
   Zep, and Graphiti for memory UX, provenance, temporal memory, and
   delete/update semantics.
3. For sandbox, compare OpenHands, E2B, and Daytona, but implement ai-platform
   lease/audit policy first.
4. For worker capacity, study Temporal/Celery patterns, but keep current
   ai-platform queue contracts unless a future migration issue proves otherwise.
5. For model gateway/cost, study LiteLLM budgets/rate limits/spend tracking and
   Portkey gateway routing/observability patterns.
6. For authorization and policy, study OpenFGA, SpiceDB, OPA, MCP Gateway, and
   supergateway, but keep ai-platform as the enforcement and audit source of
   truth.
7. For observability and quality, study Langfuse, Phoenix, OpenTelemetry,
   promptfoo, Ragas, and Giskard, but keep release evidence, redaction, admin
   projections, and workflow-owner thresholds platform-owned.
8. For Skills management, study Dify, Backstage, Open WebUI, LibreChat, and
   AnythingLLM-style UX only as workflow/release inspiration; backend authority
   remains ai-platform.

### 6.2 Near-Term Reference Reading Tasks

These are reading tasks, not implementation commitments:

1. B0: compare Keycloak, Authentik, and Ory Kratos OIDC/session/group mapping
   patterns; map only the integration vocabulary to ai-platform trusted
   principal, tenant, role, and audit projections.
2. B1: read LangGraph persistence/checkpoint and Mem0/Zep/Graphiti memory
   update/delete/temporal patterns; map them to ai-platform memory scopes,
   export, redaction, and long-term fail-closed policy.
3. B2: compare OpenHands, E2B, and Daytona sandbox lifecycle, command execution,
   artifact return, cancellation, and cleanup concepts; map them to
   ai-platform leases and 211 Docker evidence.
4. B3: compare Temporal/Celery/Dramatiq/Taskiq worker semantics and
   LiteLLM/Portkey gateway controls; map them to queue/admission,
   model-gateway backpressure, token/cost, and the 10 x 4 SDK subagent target.
5. B4: compare Backstage catalog/release metadata and Dify/Open WebUI/LibreChat
   app/tool catalog UX; map them to Skill upload, immutable versions, review,
   release, rollback, visibility, and pinned run snapshots.
6. B5: compare OpenFGA/SpiceDB relationship modeling, OPA policy tests, and MCP
   Gateway/supergateway tool catalog routing; map them to backend-owned
   ACL/tool/sandbox/Skill deny-path matrices.
7. B6: compare Langfuse, Phoenix, OpenTelemetry, promptfoo, Ragas, and Giskard
   observability/eval patterns; map them to Admin Runtime projections,
   trace/export, alert calibration, golden-set eval, and redacted release
   evidence.

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

### 8.1 Next Issue Chain

The next backend issue chain should stay narrow and evidence-first:

| Order | Issue theme | First PR goal | Why first |
| --- | --- | --- | --- |
| 1 | B0/B1 merged-source evidence refresh | Record or merge B1 fresh runtime evidence for the current source, then close only the evidence gap it actually closes. | Current readiness still distinguishes historical/runtime evidence from latest-main closure. |
| 2 | B2 real sandbox smoke | Turn the existing sandbox contracts into a 211 Docker/equivalent smoke with cleanup/orphan proof. | `sandbox_provider=fake` remains the clearest blocker to real tool/Skill execution credibility. |
| 3 | B3 capacity profile | Add bounded evidence for 10 sessions x peak 4 SDK subagents without raising defaults. | SDK subagent capability exists, but load, model-gateway, sandbox, event/artifact, and cost pressure are unproven. |
| 4 | B4 Skill lifecycle slice | Make Skill upload/version/release/rollback and dependency review operational enough for reviewed Skill runs. | Skills are the user-visible product surface; mutable folders are not enough for productization. |
| 5 | B5 file/artifact/tool deny paths | Close exact permission, file ACL, artifact preview/download, and replay risks for real document tasks. | File-heavy workflows are the first real internal value path and also the highest leakage risk. |
| 6 | B6 operations beta package | Add Admin Runtime, trace/export, alert, golden-set, rollback, and owner signoff for one named workflow. | Product beta needs supportability evidence, not only successful generated files. |

### 8.2 Capacity Target Boundary

The initial backend capacity target is deliberately small:

- 10 concurrent user sessions.
- Peak 4 Claude Agent SDK subagents per session for selected workflows.
- The target is a measurement profile first, not a default configuration.

B3 must record:

- Queue/admission behavior under the profile.
- Worker heartbeat, lease, retry, cancel, resume, and dead-letter behavior.
- Model-gateway concurrency, timeout, retry/backoff, and provider-limit behavior.
- Token/cost accounting by tenant/user/session/run/model.
- Sandbox/container pressure when the selected workflow uses sandbox.
- Event/artifact write volume and cleanup behavior.
- Rollback steps to return to the previous worker/model/sandbox profile.

If any evidence item is missing, the status is at most `local partial` or
`211 verified` for the narrower smoke. It is not `gate closable` and must not
raise production defaults.

### 8.3 Backend Product Beta Boundary

The backend becomes product-beta ready only when it supports a named internal
workflow with:

- Company login and same-tenant admin/ordinary-user behavior.
- Governed Skill selection and immutable run snapshots.
- Memory/context policy with provenance, export, delete/redaction, opt-out, and
  long-term fail-closed behavior.
- Real sandbox evidence if the workflow executes shell/script/code or other
  high-risk tools.
- File upload, artifact preview/download, and unauthorized deny evidence.
- Admin Runtime visibility into queue, worker, sandbox, model gateway,
  token/cost, latency, errors, dead letters, and release evidence.
- Workflow owner signoff, quality threshold, cost budget, alert rules, support
  owner, and rollback drill.

Until then, successful file generation or review through one Skill remains a
controlled workflow smoke, not a product-beta completion claim.
