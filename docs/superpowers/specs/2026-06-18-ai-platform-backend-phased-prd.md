# AI Platform Backend Productization PRD

> Status: active backend companion PRD for S2/S3 productization.
>
> Scope: backend route after the accepted S1 / Foundation Alpha historical
> baseline. Frontend UI absorption is governed by
> `docs/superpowers/specs/2026-06-18-librechat-frontend-ui-absorption-prd.md`.
>
> This document is a product and acceptance contract, not gate-closure
> evidence. Gate closure still requires:
> issue -> branch/PR -> review -> merge -> required 211 deploy/smoke -> issue
> closure with evidence.

## 0. Executive Decision

ai-platform is now moving from POC evidence gathering into productization. The
backend product goal is to make the existing company-internal Agent control
plane usable, governable, and operable for selected internal workflows.

The backend route is:

1. Keep Claude Agent SDK as the execution layer.
2. Do not build a second platform-level agent harness in the current stage.
3. Treat SDK Agent/subagent capability as work inside one governed platform run.
4. Productize the backend control plane around identity, tenancy, queue,
   sandbox, memory/context, Skills, files/artifacts, tool permission,
   observability, cost, and release evidence.
5. Raise capacity only after recorded load/backpressure evidence.

The four P0 backend capabilities are:

| Priority | Capability | Product target |
| --- | --- | --- |
| P0-1 | Memory/context usable | Selected workflows can use governed memory/context with provenance, opt-out, retention, delete/redaction, export, and deny-path evidence. |
| P0-2 | Real sandbox usable | A Docker/equivalent provider can run governed SDK Skill tasks; `fake` is local/test-only. |
| P0-3 | Worker/model-gateway capacity | The initial target profile, 10 concurrent sessions with peak 4 SDK subagents per session, is measured before any default increase. |
| P0-4 | Skills management | Skills move from mutable folders to upload/import, immutable versioning, release review, rollback, dependency evidence, pinned run snapshots, and audit. |

Supporting capabilities are not optional: model-gateway/cost controls,
file/artifact governance, exact tool-permission binding, and Admin Runtime
observability are required for the P0 capabilities to be safely exposed.

## 1. Source Authority And Status Language

This PRD narrows backend execution of the active product PRD:
`docs/superpowers/specs/2026-06-10-ai-platform-product-prd-v2.md`.
The task-by-task execution plan for this contract is
`docs/superpowers/plans/2026-06-20-backend-productization-prd-gates.md`.

Use these sources together:

| Source | Responsibility |
| --- | --- |
| Product PRD v2 | Product goal, global reference boundaries, S1 historical baseline, G0-G10 roadmap. |
| This backend PRD | Backend B0-B6 sequencing, entry/exit gates, evidence boundaries, reference-code intake rules. |
| Technical acceptance matrix | Module-level acceptance text and regression expectations. |
| Gate status snapshot | Current status and caveats; never gate closure by itself. |
| Release evidence records | Reviewed runtime evidence, smoke output, source/runtime relation, image labels, redacted proof artifacts. |
| Guardrails and GitHub workflow | Implementation rules, issue/PR/review/merge/211 closure loop. |

If these documents disagree, stop feature work and repair source authority
before making product claims.

All reports must use the narrowest true status:

| Status | Meaning | Minimum evidence |
| --- | --- | --- |
| `local partial` | Local docs, code, tests, static verifier, or source-contract evidence exists; no merge/runtime claim. | Targeted local verification or readiness output for the changed scope. |
| `PR ready` | A scoped PR is ready for review. | Issue link, branch, PR, changed-scope verification, and explicit boundary statement. |
| `reviewed` | Independent review was recorded and findings were resolved or tracked. | Review comment, approval, or evidence-backed review disposition. |
| `merged` | The PR landed on `main`. | GitHub merged state or merge commit. |
| `211 verified` | Runtime evidence exists for the named source/runtime subject. | Source/runtime relation, image labels, container health, smoke output, redaction scan, and reviewed evidence. |
| `gate closable` | The stage/gate issue can close. | Issue, PR, review, verification, docs, runtime evidence where required, rollback/disable path, and residual caveats. |

No docs-only PR may create `211 verified` or `gate closable` status.

### 1.1 Claim Ladder

Backend productization reports must climb the claim ladder one step at a time:

| Claim level | Meaning | Examples |
| --- | --- | --- |
| planning source | A PRD, acceptance matrix, or roadmap names a target and boundary. | B0-B6 sequencing, reference-project intake rules, non-goals. |
| local contract | Code, tests, or docs define the local behavior and fail-closed conditions. | Route tests, repository tests, verifier contracts, document-contract tests. |
| runtime evidence | A named deployed runtime subject proves the behavior on 211 or another named target. | Source/runtime relation, image labels, container health, smoke output, redaction scan. |
| stage closure | The issue/PR/review/merge/evidence loop is complete for the named backend bundle. | Issue closure evidence with residual caveats and rollback path. |
| beta readiness | One or two selected workflows have owner signoff, capacity, quality, cost, audit, support, and rollback evidence. | Operations Beta or department workflow acceptance package. |

A stage can advance only one claim level at a time. A `211 verified` runtime
smoke does not by itself create `gate closable`. A `gate closable` backend
bundle does not by itself create product beta.

### 1.2 Current Backend Productization State

This section is a planning pointer for the 2026-06-20 backend route. The live
status source remains `docs/operations/ai-platform-gate-status.md`, release
evidence, and the current readiness CLI output.

Issue #164 records B0 runtime-subject evidence for
`e4c0e9d0298c684df369afecd29ec902fcc2221d` after PR #206. Source/runtime/image
labels, API/worker health, Foundation Runtime concurrency, redaction, runtime
POC smoke, and runtime export/trace acceptance are recorded for that named
runtime subject. This is not `gate closable`, does not prove current-source
runtime verification for later `main` commits, and does not close full G0 source
authority because the rollout used a runtime-only marker rebase workaround, the
compose env-file label still points to an external env-file path, production auth
rollout remains separate, and future runtime-affecting source changes reopen B0
until fresh source, runtime, image, health, concurrency, redaction, and review
evidence is recorded. This does not close B1/B2/B3 product gates, raise
production concurrency defaults, claim Docker sandbox hardening, or enable
ordinary-user multi-agent exposure.

| Stage | Current planning status | Active next boundary |
| --- | --- | --- |
| B0 | Reviewed runtime-subject evidence exists for `e4c0e9d`; not `gate closable`, and not current-source verified after later runtime-affecting `main` changes. | Reopen when readiness reports runtime rollout, source/runtime drift, or a runtime-affecting merge. Reconcile the env-file label caveat before any full G0 closure claim. |
| B1 | `local partial`; source contracts and named runtime evidence exist, but the stage is not `gate closable`. | Choose one governed document workflow and prove memory/context through `live_worker_run_payload` plus rollback and deny paths. |
| B2 | `local partial`; fake provider remains local/test-only and controlled probes do not prove governed SDK Skill execution. | Prove Docker/equivalent sandbox through a governed Skill run with lease, callback, resource, egress, artifact, cancel, cleanup, and redaction evidence. |
| B3 | `local partial`; source contract only for `b3_10x4_sdk_subagents`; production defaults stay unchanged. | Record an operator-reviewed capacity snapshot for 10 sessions x peak 4 SDK subagents/session, including model-gateway, sandbox, token/cost, event/artifact, and cleanup pressure. |
| B4 | `local partial`; mutable folders and image-copied Skills are not production authority. | Build upload/import, immutable versioning, release review, rollback, dependency evidence, pinned run snapshots, and authorized visibility. |
| B5 | `local partial`; file/artifact ACL and exact tool decisions must close separately. | Prove file/artifact authority and shell/MCP/filesystem decision replay denial as separate sub-contracts. |
| B6 | `local partial`; no product beta claim. | Package one or two named internal workflows with owners, SLO/cost/quality, support, rollback, and linked B1-B5 evidence. |

Near-term backend execution order is therefore B1/B2/B3/B4, with B5 and B6
following after the capability substrate is credible:

| Execution order | Backend question | Gate boundary |
| --- | --- | --- |
| 1 | Can one selected document workflow use governed memory/context safely? | B1 live worker payload, provenance, redaction, delete/export, rollback, deny paths. |
| 2 | Can a real sandbox provider run a governed SDK Skill path safely? | B2 Docker/equivalent lease, callback, egress/resource policy, artifact return, cancel, cleanup, projection safety. |
| 3 | Can 10 sessions x peak 4 SDK subagents/session run without breaking queue, worker, model gateway, sandbox, cost, events, artifacts, or cleanup? | B3 operator-reviewed recorded snapshot; no default increase until reviewed. |
| 4 | Can Skills become a managed product surface instead of mutable folders? | B4 immutable version, release review, dependency evidence, pinned snapshots, rollback, visibility. |
| 5 | Can files/artifacts and high-risk tools be governed without replay or ACL bypass? | B5a file/artifact authority and B5b exact tool decisions close independently. |
| 6 | Can one or two real internal workflows be accepted by owners and operators? | B6 owner signoff, SLO/cost/quality/support/rollback, linked B1-B5 evidence. |

## 2. Product Non-Goals

| Non-goal | Boundary |
| --- | --- |
| Replacing ai-platform with Dify, Open WebUI, LibreChat, LangGraph, Temporal, or another backend | External projects are references only. ai-platform owns identity, tenancy, RBAC, audit, source authority, release evidence, and gate closure. |
| Building a separate platform-level multi-run agent harness now | SDK Agent/subagent behavior remains execution-kernel fanout inside one governed platform run. Platform-level parent/child run orchestration is deferred. |
| Raising worker/model defaults by configuration only | B3 must prove the selected profile with load, backpressure, token/cost, sandbox pressure, cleanup, and rollback evidence. |
| Treating `fake` sandbox as production evidence | `fake` is only local/test proof. B2/G7 requires Docker/equivalent evidence on a Docker-capable host. |
| Enabling long-term memory by default | Long-term memory remains fail-closed until policy, retention, export, delete/redaction, opt-out, and tenant deny paths are proven. |
| Treating mutable Skill folders as production authority | Production Skill execution requires immutable versions, release state, dependency evidence, pinned snapshots, and used-skill audit. |
| Letting frontend UI shape backend authority | LibreChat/Open WebUI-style UX can influence the shell, Skills discovery, and slash-command surfaces, but backend authority stays in ai-platform contracts and routes. |

## 3. Backend Stage Model

B0-B6 are backend planning and acceptance bundles. They do not replace G0-G10
product gates.

Sequencing rule:

- B0 runs first whenever source/runtime evidence is stale or ambiguous.
- After B0, the product order is B1 memory/context, B2 real sandbox, B3
  worker/model-gateway capacity, then B4 Skills management.
- B5 and B6 are supporting product gates that must be advanced alongside the
  workflow they protect; they are not optional cleanup after beta.
- A runtime blocker can pause the sequence, but it must be recorded as a named
  issue instead of silently reordering the roadmap.

| Stage | Name | Product target | Gate mapping | Default status before evidence |
| --- | --- | --- | --- | --- |
| B0 | Latest-main backend readiness refresh | Current source, 211 source, deploy composition, runtime labels, backend/worker containers, and readiness tools agree. | G0-G1, S2-0 | `local partial` |
| B1 | Memory/context usable | Selected workflows can use governed memory/context with provenance, policy, retention, delete/redaction, export, and deny paths. | G6, G9 | `local partial` |
| B2 | Real sandbox usable | Docker/equivalent sandbox runs governed SDK Skill tasks with leases, quotas, egress policy, callback validation, cleanup, and artifact return. | G7, G6, G9 | `local partial` |
| B3 | Worker/model-gateway capacity | Target profile is measured before defaults increase: initial profile is 10 sessions x peak 4 SDK subagents/session. | G5, G8, G9 | `local partial` |
| B4 | Skills management and release governance | Skills can be uploaded/imported, versioned, reviewed, released, rolled back, pinned, dependency-reviewed, and audited. | G6, G9 | `local partial` |
| B5 | Files/artifacts/tool permission governance | File upload, artifact ACL, preview/download, exact tool approvals, MCP/shell/filesystem policy, and replay denial are proven. | G6, G7, G9 | `local partial` |
| B6 | Operations beta and workflow readiness | Admin Runtime, trace/export, alerting, golden-set eval, workflow owner signoff, rollback, and support model exist. | G9, G10 | `local partial` |

## 4. Gate Register

Every backend issue must name the smallest gate it is moving. A PR may touch
multiple gates only when the acceptance evidence is shared and the PR body
states the boundary explicitly. The gate name is not a status claim; it is a
scope label for review, verification, runtime smoke, and issue closure.

| Stage | Smallest gate | Gate name | Required closure evidence |
| --- | --- | --- | --- |
| B0 | B0-G1 | Source/runtime identity | Current `main`, 211 source path, repo-local deploy composition, image labels, API/worker image IDs, and `/api/ai/health` agree on the named subject. |
| B0 | B0-G2 | Readiness contract | `tools/foundation_alpha_readiness.py --format json` and source-authority tests report current-source blockers without mixing S1 historical evidence into latest-main status. |
| B0 | B0-G3 | Runtime concurrency evidence | Foundation Runtime concurrency evidence is bound to the current runtime subject when readiness requires it. |
| B0 | B0-G4 | Evidence hygiene | Evidence is redacted, reviewed, and free of real `.env`, secrets, private executor payloads, and personal workstation paths. |
| B0 | B0-G5 | Caveat classification | Legacy env-label, runtime-only rebase, dirty source, deployment workaround, or stale source caveats are classified as blocking or non-closing follow-ups. |
| B1 | B1-G1 | Memory policy | Session/project/workspace/long-term scope, retention, opt-out, export, delete/redaction, and rollback policy are explicit and tested. |
| B1 | B1-G2 | Context provenance | Context snapshots and packs record source documents, run, user, tenant/workspace, selection reason, context-pack version, and redaction state. |
| B1 | B1-G3 | Deny paths | Cross-user, cross-tenant, wrong-run, deleted/redacted memory, disabled-policy reads, and stale context-pack reads fail closed. |
| B1 | B1-G4 | Projection safety | Public/admin projections do not leak raw memory content, executor private payloads, raw storage keys, backend paths, or sandbox paths. |
| B1 | B1-G5 | Workflow smoke | One selected workflow proves governed memory/context use on 211 with provenance, redaction, artifact/event relation, export/delete posture, and rollback/cleanup path. |
| B2 | B2-G1 | Real provider selection | Docker/equivalent provider is selected for runtime claims and `fake` is explicitly local/test-only. |
| B2 | B2-G2 | Lease and ownership | Lease create/refresh/release, tenant/user/run ownership, callback token, token mismatch denial, quota denial, and cleanup failure handling are tested. |
| B2 | B2-G3 | Hardening policy | Resource limits, security options, egress/network posture, filesystem/workdir isolation, and secret/env handling are recorded and reviewed. |
| B2 | B2-G4 | Runtime lifecycle | 211 smoke proves launch, command execution, artifact return, cancel, cleanup, and orphan scan for the named source/runtime subject. |
| B2 | B2-G5 | Bounded failure projection | Admin Runtime or release evidence shows sandbox state/failures without private payloads, raw container paths, callback secrets, or executor internals. |
| B2 | B2-G6 | Rollback/disable path | Operators can disable real sandbox or revert provider/profile without deleting audit or run evidence. |
| B3 | B3-G1 | Target profile | The measured profile is named: initial target is 10 concurrent sessions and peak 4 SDK subagents per session for selected workflows only. |
| B3 | B3-G2 | Admission and fairness | Tenant-aware and user-aware quota/backpressure, bounded queue metadata, admission denial, retry/cancel/dead-letter behavior, and worker heartbeat are tested. |
| B3 | B3-G3 | Model gateway policy | Provider/model selection, timeout, retry/backoff, provider-limit handling, cost budget, token accounting, and fail-closed provider policy are tested. |
| B3 | B3-G4 | Runtime load evidence | 211/named-target load evidence records API burst, run creation burst, queue depth, lease latency, worker start/terminal behavior, model pressure, sandbox pressure if used, cleanup, events/artifacts, token/cost, and rollback. |
| B3 | B3-G5 | No default raise by implication | Higher env values, more workers, or a fast single run do not raise production defaults; a separate explicit change is required after reviewed evidence. |
| B4 | B4-G1 | Skill package contract | Upload/import validates manifest, files, size, dependency metadata, entrypoint, allowed tools, owner, tenant/workspace visibility, and checksum. |
| B4 | B4-G2 | Immutable versioning | Skill versions are immutable, addressable by stable version ID/digest, and cannot be silently overwritten by mutable folders. |
| B4 | B4-G3 | Release workflow | Draft/reviewed/released/disabled/deprecated states, release approval, rollback, and ordinary-user denial for unreviewed or disabled Skills are tested. |
| B4 | B4-G4 | Dependency evidence | SBOM/license/vulnerability/dependency review is stored or a gate-limited exception is recorded before release. |
| B4 | B4-G5 | Pinned run snapshot | Runs record exact Skill version, manifest digest, dependency evidence reference, selected files, and used-skill snapshot. |
| B4 | B4-G6 | Reviewed runtime Skill run | One released Skill run on 211 goes through queue/worker/sandbox where applicable and records artifacts, release decision, dependency summary, and redaction result. |
| B5 | B5-G1 | File namespace and retention | Upload namespace binds tenant/workspace/user/run/file, retention, scan/redaction state, size/type policy, and cleanup. |
| B5 | B5-G2 | Artifact ACL | Artifact preview/download enforces owner/tenant ACL and denies cross-user, cross-tenant, wrong-run, deleted, and expired artifacts. |
| B5 | B5-G3 | Exact tool permission | Shell/network/MCP/filesystem/write-capable approvals bind to `tool_call_id` or stable request fingerprint, input hash, run, user, tenant, expiry, and replay denial. |
| B5 | B5-G4 | Projection redaction | Public/admin views do not expose raw storage keys, local paths, private executor payloads, secrets, or unredacted document text. |
| B5 | B5-G5 | Runtime workflow proof | 211 smoke proves file upload -> governed run -> artifact preview/download -> unauthorized denial for a selected document workflow. |
| B6 | B6-G1 | Admin Runtime coverage | Operators can inspect queue, worker, sandbox, model gateway, token/cost, latency, errors, dead letters, events/artifacts, and release evidence without reading chat transcripts. |
| B6 | B6-G2 | Trace/export | Traces and release-evidence exports are redacted, source/runtime-bound, reviewer-friendly, and reproducible from stored evidence. |
| B6 | B6-G3 | Alerting and support | Alerts, support owner, escalation path, SLO/SLA, and runbook exist for selected workflows. |
| B6 | B6-G4 | Quality gate | Golden-set eval, acceptance threshold, failure triage, and regression baseline exist for the selected workflow. |
| B6 | B6-G5 | Owner signoff and rollback drill | Workflow owner accepts capacity, quality, cost, audit, support, and rollback evidence; rollback drill is recorded. |

Backend-only closure for B4-B6 requires public/admin projection contracts,
route behavior, evidence records, and deny paths. Browser visual acceptance,
frontend shell absorption, and detailed interaction polish are tracked by the
frontend UI absorption PRD. A backend PR must still expose enough safe
projection for the frontend to build against; it does not have to close visual
acceptance unless the issue explicitly includes that scope.

## 5. Acceptance Boundaries By Stage

Each stage has four boundaries:

- Entry gate: when the team is allowed to start implementation.
- Local acceptance: what a PR must prove before review.
- Runtime acceptance: what 211 or another named Docker-capable target must
  prove when runtime behavior is claimed.
- Exit gate: what must be true before the stage is `gate closable`.

### 5.1 Required Evidence Shape

Every backend stage issue, PR body, runtime evidence, or closure comment should
use this evidence shape. The goal is to make the claim level auditable without
reading chat history.

| Field | Required content |
| --- | --- |
| `issue_or_decision` | GitHub issue, no-code decision, or direct-main exception that authorizes the slice. |
| `source_subject` | Commit, branch, source tree, runtime subject, and whether the claim is exact-current-source or runtime-relevant-source only. |
| `local_verification` | Targeted commands, tests, static verifiers, and known local blockers. |
| `runtime_verification` | 211 or named-target smoke, image/container identity, source/runtime relation, redaction result, and captured artifacts. |
| `review_disposition` | Independent review status, unresolved findings, rejected findings with evidence, and follow-up issues. |
| `residual_caveats` | Non-closing caveats such as deployment-layout/env-file drift, runtime-only rebase workaround, or partial visual acceptance. |
| `non_expansion_invariants` | Explicit booleans that prevent a narrow smoke from being read as broader production approval. |
| `rollback_or_disable_path` | Operator action that restores prior runtime/configuration or disables the capability. |

Use these evidence levels consistently in issues, PR bodies, readiness output,
release evidence, and closure comments:

| Evidence level | Meaning | Cannot close |
| --- | --- | --- |
| `source_contract` | Code, tests, static verifier contracts, or docs define expected behavior and fail-closed conditions. | Runtime acceptance, 211 verification, or production exposure. |
| `source_probe_on_target_runtime` | A target host can import, inspect, or bind the current source path, but no live governed run proves the behavior. | 211 acceptance for the governed runtime path. |
| `controlled_live_probe` | A controlled verifier proves a bounded path on a named runtime. | Broad production hardening, ordinary-user exposure, or workflow beta. |
| `live_worker_run_payload` | A real platform run and worker payload prove the governed path, including platform-issued run, context, Skill, artifact, and event relations. | Broader stage closure without issue, review, rollback, and residual-caveat evidence. |
| `live_platform_probe` | Platform API, worker, queue, sandbox, or artifact state proves a live target resource lifecycle. | Source-regression-only controls, such as timeout/failure fallback, unless separately tested. |
| `operator_reviewed_recorded_snapshot` | A redacted, source-bound, reviewed evidence snapshot is accepted by operators. | Product beta without named workflow owner signoff and support/rollback evidence. |

Do not collapse these levels. For example, a `controlled_live_probe` can close a
named runtime-smoke gap, but it cannot by itself close B2/G7 hardening. A
`source_probe_on_target_runtime` can support source authority, but it is not a
live worker-run acceptance proof.

Use these default `non_expansion_invariants` unless a later issue explicitly
changes and proves them:

- `production_concurrency_defaults_raised=false`
- `ordinary_user_platform_multi_run_orchestration_enabled=false`
- `docker_sandbox_hardening_claimed=false`
- `long_term_cross_session_memory_default_enabled=false`
- `department_rollout_allowed=false`

### 5.2 B0 Latest-Main Backend Readiness Refresh

**Entry gate**

- Latest `main` and current runtime subject are identified.
- 211 backend source path, repo-local deploy composition path, API/worker
  container names, image tag, and readiness command are known.
- A GitHub issue exists when runtime evidence is required.

**Local acceptance**

- `tools/foundation_alpha_readiness.py --format json` was run with current
  source.
- Source-authority and readiness tests for the changed scope pass.
- The result names every current-source blocker without mixing it with the S1
  historical baseline.

**Runtime acceptance**

- 211 source marker, source snapshot, compose labels, image labels, API/worker
  container image, and `/api/ai/health` agree on the named subject.
- Current-subject Foundation Runtime concurrency evidence exists when readiness
  requires it.
- Evidence is redacted and reviewed.

**Exit gate**

- Readiness has no current-source stage blocker for the claimed scope.
- Remaining caveats, such as legacy env-file label caveats, are named as
  non-closing follow-ups.
- Status may become `211 verified`; it is `gate closable` only after issue/PR
  closure evidence is complete.

**Cannot claim**

- A historical S1 baseline does not prove latest-main readiness.
- A local readiness pass without matching runtime evidence is not `211 verified`.

### 5.3 B1 Memory And Context Usable

Minimum product slice: one named document workflow can use a governed
session/workspace-scoped context pack with provenance. Long-term cross-session
memory stays disabled by default unless a separate issue proves the full
long-term memory policy matrix.

**Entry gate**

- A concrete workflow is selected.
- Memory scope is named: session, project/workspace, or policy-bound long-term;
  the default allowed scope is session/workspace only.
- Retention, opt-out, delete/redaction, export, context-pack provenance, and
  rollback boundaries are specified.
- Long-term memory remains fail-closed unless the issue explicitly changes that
  policy and proves the full acceptance matrix.

**Local acceptance**

- Tests cover memory policy create/update, context snapshot creation, context
  pack selection, provenance, retention, delete/redaction, export metadata, and
  at least one cross-user or cross-tenant denial.
- Public/admin projections do not expose raw memory content, executor private
  payloads, raw storage keys, or sandbox paths.
- Export surfaces are allowlisted projections only: bounded metadata, redacted
  summaries, policy state, provenance, and audit references. Raw memory content,
  file bodies, artifact bodies, storage keys, backend paths, and sandbox paths
  are not exported by the B1 backend contract unless a later issue explicitly
  proves that surface.

**Runtime acceptance**

- A `live_worker_run_payload` smoke on 211 or another named target proves the
  selected workflow uses memory/context through a governed platform run.
- Evidence records `context_pack_version`, `context_pack_generated_at`, bounded
  public provenance, selected policy, redaction result, artifact/event relation,
  retention/delete/export posture, and rollback or cleanup path.
- If only a source contract, source probe, or controlled verifier exists, B1
  remains `local partial` even when a named runtime gap is closed.

**Exit gate**

- Memory is enabled only for the selected governed scope.
- Long-term memory, if still not fully proven, remains disabled by default.
- The issue records owner-facing behavior and operator rollback.
- Closure evidence includes non-expansion invariants for
  `long_term_cross_session_memory_enabled=false`,
  `public_projection_only_for_ordinary_users=true`, and
  `stores_private_executor_material_as_memory=false` unless an explicit later
  gate changes those values.

**Cannot claim**

- A memory table or route alone does not make memory usable.
- Session memory smoke does not prove long-term memory.
- Runtime evidence for one document workflow does not close broader G6/G9,
  department rollout, or every future memory scope.

### 5.4 B2 Real Sandbox Usable

Minimum product slice: a real provider executes a governed SDK Skill path under
platform lease, policy, callback, artifact, cleanup, and projection controls.
Standalone verifier execution and worker-process task execution are not B2
product proof.

**Entry gate**

- A real provider is selected: Docker provider or explicitly named equivalent.
- Lease lifecycle, ownership, callback token, quota, egress/network policy,
  security options, artifact return, cancel, cleanup, orphan scan, and rollback
  are designed before exposure.

**Local acceptance**

- Tests cover provider selection, lease creation/refresh/release, callback
  validation, deny-on-token mismatch, quota failure, cleanup failure, cancel,
  orphan scan, and projection redaction.
- Fake-provider tests remain local/test-only and cannot satisfy runtime claims.

**Runtime acceptance**

- 211 Docker/equivalent evidence is recorded at the right evidence level:
  - `source_contract` for provider, lease, callback, failure, cleanup, and
    projection tests.
  - `controlled_live_probe` for bounded verifier launch, command execution,
    artifact return, cancel, cleanup, orphan scan, and private-projection scan.
  - `live_worker_run_payload` for a governed SDK Skill run that actually uses
    the real provider through the platform worker path.
  - `operator_reviewed_recorded_snapshot` before B2/G7 hardening is considered
    closable.
- Evidence includes image/container identity, sandbox profile, resource limit,
  egress posture, callback validation, cancel behavior, artifact return,
  cleanup result, and the distinction between `live_platform_probe` checks and
  `source_regression_guard` checks.

**Exit gate**

- `fake` is not used as production proof.
- The real provider has fail-closed behavior and rollback instructions.
- Operators can see sandbox lease state and failures through Admin Runtime or
  release evidence.

**Cannot claim**

- A successful SDK task in the worker process is not sandbox proof.
- Docker provider source code without 211 smoke is not production-ready sandbox.
- A standalone Docker verifier does not prove the governed SDK Skill path unless
  it is bound to a platform run, Skill snapshot, sandbox lease, callback policy,
  artifacts, events, and cleanup evidence.
- A verifier-owned Docker-only probe does not prove platform-issued sandbox
  cleanup or over-limit behavior.
- Bounded latency or smoke evidence does not close resource-limit policy,
  egress policy, security-option hardening, ordinary-user high-risk tool
  exposure, or broader G7 production sandbox hardening.

### 5.5 B3 Worker And Model-Gateway Capacity

Minimum product slice: prove the selected profile before changing defaults.
The initial profile is 10 concurrent user sessions with peak 4 Claude Agent SDK
subagents per session for selected workflows. This is a recorded capacity gate,
not Foundation Runtime concurrency correctness evidence.

**Entry gate**

- Target profile is named. Initial profile:
  - 10 concurrent user sessions.
  - Peak 4 Claude Agent SDK subagents per session.
  - Selected workflows only.
- Tenant mix, user mix, selected Skill/workflow mix, model/provider route,
  sandbox involvement, timeout budget, stop condition, and rollback plan are
  named before the run.
- Existing production defaults remain unchanged until exit evidence is reviewed.

**Local acceptance**

- Load harness or bounded smoke tool records queue depth, lease latency, worker
  heartbeat, active-run admission, retry/cancel/dead-letter behavior,
  model-gateway timeout/backpressure, token/cost accounting, event/artifact
  volume, and cleanup.
- Tests prove tenant-aware quota/backpressure, user-aware limits, bounded queue
  metadata, and fail-closed model-gateway policy.
- The local contract defines pass/fail thresholds for p95/p99 latency, error
  budget, retry/cancel/dead-letter behavior, cleanup completion, token/cost
  ledger completeness, event/artifact volume, and model-gateway timeout or
  provider-limit responses.
- The B3 operator-reviewed recorded snapshot source contract is emitted by the
  capacity profile readiness path with schema
  `ai-platform.capacity-operator-reviewed-recorded-snapshot-contract.v1`.
  The target profile is `b3_10x4_sdk_subagents`: 10 sessions x peak 4 SDK
  subagents/session, equivalent to 10 concurrent user sessions with
  peak 4 Claude Agent SDK subagents per session. This local contract records
  expected fields and fail-closed flags only; it does not raise production defaults or claim safe concurrency,
  does not enable ordinary-user multi-agent exposure,
  and does not close B3 or G8/G9.

**Runtime acceptance**

- 211 or named target records an `operator_reviewed_recorded_snapshot` covering
  the seven evidence gates:
  1. API burst behavior.
  2. Run creation burst behavior.
  3. Queue depth and lease latency.
  4. Worker start/heartbeat/terminal behavior.
  5. Model-gateway timeout, retry/backoff, provider-limit, and backpressure.
  6. Sandbox/container pressure when workflow uses sandbox.
  7. Cleanup, dead-letter, event/artifact volume, token/cost, and rollback.
- The snapshot records submitted count, completed count, failed count, cancelled
  count, p50/p95/p99 latency where applicable, max queue depth, lease latency,
  worker heartbeat gaps, model-gateway errors/backpressure, sandbox pressure,
  token/cost totals, cleanup proof, stop-condition status, and residual
  caveats.
- The B3 operator-reviewed recorded snapshot must also record these source-bound
  review evidence fields: `runtime_source_identity_and_image_labels`,
  `tenant_user_skill_mix`, `token_cost_ledger`, `event_artifact_volume`,
  `sandbox_pressure_and_cleanup`, `latency_p50_p95_p99`,
  `error_budget_and_dead_letters`, and `rollback_plan_and_stop_conditions`.

**Exit gate**

- Operator-reviewed evidence proves the selected profile.
- Defaults are raised only by a separate explicit change after evidence review.
- Rollback restores previous worker/model/sandbox profile.

**Cannot claim**

- More worker processes or higher env values are not capacity proof.
- One fast run does not prove SDK subagent fanout pressure.
- Foundation Runtime concurrency evidence proves controlled POC correctness; it
  does not close the B3 capacity-upgrade evidence gate.
- A bounded probe or draft evidence bundle is not recorded gate evidence until
  an operator-reviewed snapshot accepts it.

### 5.6 B4 Skills Management And Release Governance

Minimum product slice: a run uses an immutable, reviewed platform Skill version
and records a pinned run snapshot. A governed Skill run proves execution
governance only; production Skill release also requires package/dependency
review evidence.

**Entry gate**

- Skill lifecycle state model is agreed:
  upload/import, immutable version, validation, dependency metadata,
  release review, disable/deprecate, rollback, visibility, and pinned run
  snapshots.

**Local acceptance**

- Tests cover upload/import validation, version immutability, release state
  transitions, rollback, disabled/unreviewed Skill denial, cross-tenant denial,
  dependency metadata, and `run_skill_snapshots` pinning.
- Runs cannot read global mutable Skill folders as production authority.

**Runtime acceptance**

- One reviewed Skill run on 211 goes through queue/worker/sandbox where
  applicable.
- Evidence records selected Skill version, used-skill snapshot, artifacts,
  release decision, dependency review summary, and redaction result.
- The run proves it used platform release state, immutable package or digest
  identity, dependency metadata, and `run_skill_snapshots`; it must not rely on
  a mutable repo folder, mutable image folder, or user-selected filesystem path
  as production authority.
- A gate-limited exception can permit a named controlled Skill run without full
  production package evidence only when it records the exception scope,
  rollback, dependency caveats, non-expansion invariants, and follow-up issue.
  It cannot waive tenant visibility, disabled/unreviewed Skill denial, pinned
  snapshot evidence, redaction, or audit.

**Exit gate**

- Skills have reviewed SBOM/license/vulnerability/dependency evidence or an
  explicit gate-limited exception.
- Ordinary users see only Skills they are authorized to use.
- Admins can inspect release state, dependency state, used-skill evidence, and
  rollback history without private executor payload leaks.

**Cannot claim**

- Copying a Skill directory into an image is not Skills management.
- A successful ad hoc Skill run does not prove release governance.
- Pinned Skill execution does not prove SBOM, license, vulnerability, signed
  package, Admin visual acceptance, or 211 Skill release dashboard acceptance.

### 5.7 B5 Files, Artifacts, And Tool Permission Governance

Minimum product slice: file/artifact authority and exact tool decisions are two
sub-contracts under one stage. Evidence for one sub-contract cannot close the
other.

**Entry gate**

- A high-risk workflow family is selected.
- File namespace, artifact ACL, preview/download, retention, exact tool
  decision, replay denial, MCP/shell/filesystem policy, and redaction rules are
  specified.

**Local acceptance**

- B5a file/artifact authority tests cover upload namespace, file owner/tenant
  ACL, artifact owner/tenant ACL, preview/download authorization, preview
  allowlist, retention, redacted metadata export, and cross-user/cross-tenant
  denial.
- B5b exact tool decision tests cover shell, network, MCP, filesystem, and
  write-capable operations; decisions bind to `tool_call_id` or a stable request
  fingerprint; `allow_once` cannot replay across unrelated calls; stale,
  expired, disabled, or wrong-run decisions fail closed.

**Runtime acceptance**

- 211 smoke proves file upload -> governed run -> artifact preview/download ->
  unauthorized denial for the selected workflow.
- Evidence records principal, tenant/workspace/user/run/file/artifact relation,
  permission decisions, and redaction outcome.
- If the workflow uses write-capable shell, network, MCP, or filesystem tools,
  the runtime smoke must also prove exact permission decision binding and replay
  denial. Otherwise B5b remains open even when B5a passes.

**Exit gate**

- Ordinary-user file workflows cannot bypass backend ACL, exact tool
  permission, or redaction controls.
- Admin evidence remains projection-safe and does not reveal executor private
  payloads.

**Cannot claim**

- Admin download success does not prove ordinary-user ACL safety.
- Broad "latest allow" decisions do not satisfy exact tool approval.
- File upload/download denial evidence does not prove shell, MCP, network, or
  filesystem permission safety.

### 5.8 B6 Operations Beta And Department Workflow Readiness

Minimum product slice: one or two named internal workflows have owners and
evidence packages. Abstract platform readiness is not Operations Beta.

**Entry gate**

- One operations package or department workflow package has owner, SLO, capacity
  profile, cost budget, quality gate, alert, trace/export, rollback, and support
  requirements.
- The workflow package names the workflow owner, business owner, support owner,
  tenant/workspace scope, expected SDK subagent fanout, required Skills, file
  and artifact surfaces, memory/context scope, sandbox/tool risk class, model
  provider policy, alert route, and rollback drill.
- The package links the relevant B1-B5 evidence or explicitly records which
  stage remains non-closing for the workflow.

**Local acceptance**

- Tests or static verifiers cover Admin Runtime views, trace/export, alert
  policy, golden-set eval packaging, release-evidence export, owner metadata,
  rollback metadata, and ordinary-user denial.

**Runtime acceptance**

- 211 evidence proves Admin Runtime, trace/export, alert calibration,
  golden-set evaluation, workflow smoke, rollback drill, and owner signoff for
  the selected workflow.
- Evidence records workflow SLO, p95/p99 or owner-approved latency threshold,
  quality threshold, cost budget, token/cost actuals, alert delivery, support
  handoff, rollback timing, and residual caveats.

**Exit gate**

- A named workflow owner accepts capacity, quality, audit, cost, support, and
  rollback evidence.
- Operators can diagnose queue, worker, sandbox, model gateway, cost, latency,
  errors, dead letters, and release evidence without reading chat transcripts.

**Cannot claim**

- A generated final document is workflow success, not operations beta.
- Observability screenshots without redacted evidence and owner signoff are not
  gate closure.
- B6 cannot be `gate closable` without a named workflow owner and linked B1-B5
  evidence package.

## 6. Universal Gate Closure Checklist

Before any backend stage is called `gate closable`, all items must be true:

- A GitHub issue or explicit no-code decision exists.
- A branch/PR or documented direct-main exception exists.
- Targeted local verification ran for the changed scope.
- Independent review ran when required; each finding was fixed, rejected with
  evidence, or tracked as a follow-up issue.
- Runtime work has 211 or named-target evidence with source/runtime relation,
  image labels, health, smoke output, and redaction.
- Docs or roadmap updates name the exact status change and caveats.
- Deployment workarounds, such as runtime-only image rebase, are labeled as
  workarounds.
- Rollback or disable path is recorded.
- The issue can close without relying on chat-only evidence.

Universal blockers:

- Stale source/runtime labels.
- Unreviewed release evidence.
- Real `.env` values, secrets, raw storage keys, private executor payloads, or
  personal workstation paths in committed evidence.
- `fake` sandbox evidence used for production sandbox claims.
- Capacity default increases without B3 evidence.
- SDK subagent fanout outside queue/admission/cost/event/artifact governance.
- Long-term memory enabled by default without B1 full acceptance.
- Treating `source_contract`, `source_probe_on_target_runtime`, or
  `controlled_live_probe` as broader runtime hardening or beta evidence.
- B6 or product-beta claims without a named workflow owner, support owner,
  rollback drill, and linked B1-B5 evidence package.
- Reference code copied without repository, commit/tag, license, provenance,
  tests, review, and runtime evidence where applicable.

## 7. Reference Code Projects

Reference projects can shape implementation choices, tests, and UI vocabulary.
They do not define ai-platform authority. Every use of reference code must be
classified before implementation.

### 7.1 Intake Levels

| Level | Meaning | Required evidence |
| --- | --- | --- |
| Concept-only reference | Borrow vocabulary, workflow shape, UX pattern, or test-matrix idea. | PR body names the source and explains the borrowed concept. |
| Confirmed repository reference | Use a public repository as a bounded source for product vocabulary, architecture comparisons, or test ideas. | Issue or PR records repository owner/name, URL, reviewed commit or tag when used deeply, license posture, and the exact concept borrowed. |
| Unconfirmed concept reference | Mention a project, product, pattern, or ecosystem artifact before a verified repository/source is selected. | Keep it out of implementation requirements until a repository, commit or tag, license, and intake level are recorded. |
| Code adaptation candidate | Adapt a small bounded implementation or test pattern. | Issue records project, commit/tag, license, copied/adapted files, and ai-platform tests. |
| Runtime dependency proposal | Add a library, sidecar, service, hosted API, or gateway to the running stack. | Separate architecture issue, security review, deployment plan, rollback, 211 smoke, and release evidence. |

Unconfirmed project names stay concept-only until a repository, commit or tag,
license, and intake level are recorded. Even confirmed repositories remain
references unless a separate architecture issue proposes code adaptation or a
runtime dependency.

### 7.2 Reference Matrix By Backend Stage

| Stage | Reference projects | Useful for | Not allowed to define |
| --- | --- | --- | --- |
| B0 source/auth baseline | Keycloak, Authentik, Ory Kratos | OIDC/session, group/role mapping, admin login, enterprise identity integration vocabulary. | ai-platform tenant mapping, RBAC enforcement, audit, source authority. |
| B1 memory/context | LangGraph, Mem0, Zep, Graphiti | Checkpointing, short-term vs long-term memory, memory update/delete UX, temporal/provenance concepts. | `memory_records`, context snapshots, tenant policy, retention, delete/redaction authority. |
| B2 sandbox | OpenHands, E2B, Daytona, gVisor, Kata Containers, Firecracker, Anthropic Sandbox Runtime | Workspace isolation, sandbox lifecycle, command execution, artifact return, cancel, cleanup, callback/egress patterns, resource boundaries, and isolation model comparison. | Docker socket trust, external SaaS authority, bypassing ai-platform lease/audit/sandbox policy. |
| B3 worker/model gateway | Temporal Python SDK, Celery, Dramatiq, Taskiq, LiteLLM, Portkey | Durable retry vocabulary, worker scaling, gateway routing, rate limits, budgets, provider fallback, token/cost concepts. | Replacing current queue/worker/model policy without a migration gate; external cost authority. |
| B4 Skills management | Backstage, Dify, Open WebUI, LibreChat, AnythingLLM | Catalog metadata, release workflow, app/agent marketplace UX, slash/tool discovery patterns. | Skill execution policy, tenant visibility, release authority, backend runtime lifecycle. |
| B5 files/tools/authz | OpenFGA, SpiceDB, Casbin, Open Policy Agent, ContextForge MCP Gateway, MCP Gateway Registry, labspace MCP Gateway, mcp-supergateway | Relationship-based ACLs, RBAC/ABAC policy tests, policy bundles, MCP/tool catalog routing, decision logs. | Backend ACL enforcement, exact tool decisions, audit, tenant/run/file/artifact authority. |
| B6 observability/quality | Langfuse, Phoenix, OpenTelemetry Collector, promptfoo, Ragas, Giskard | Trace/span vocabulary, token/cost dashboards, eval runs, golden-set regression, telemetry export, redaction review. | Release evidence authority, private payload storage, workflow-owner acceptance thresholds. |

### 7.3 Repository Reference Inventory

The following repository names were checked for this PRD update and are allowed
as a repository inventory for planning and comparison. They are not approved
dependencies, and this inventory is not a `Confirmed repository reference`
intake decision for every row. The check recorded the public GitHub repository,
default branch, license posture reported by GitHub, and latest observed
activity on 2026-06-19. Re-check before deep code review because these are live
projects.

Repository names in this inventory were rechecked with `gh repo view` on
2026-06-20 for PR #139 follow-up review. That recheck confirms repository
resolution only; it does not pin commits, approve licenses, approve
dependencies, or change any code-adaptation intake state.

| Area | Repository reference | Default branch | License posture | Observed activity | Use only for |
| --- | --- | --- | --- | --- | --- |
| B0 source/auth baseline | [`keycloak/keycloak`](https://github.com/keycloak/keycloak) | `main` | Apache-2.0 | 2026-06-19T11:14:21Z | OIDC, group/role mapping, and admin-login vocabulary; not ai-platform tenant/RBAC authority. |
| B0 source/auth baseline | [`goauthentik/authentik`](https://github.com/goauthentik/authentik) | `main` | GitHub reports `Other` | 2026-06-19T11:42:19Z | Identity-provider UX and policy concepts only unless license/provenance review approves intake. |
| B0 source/auth baseline | [`ory/kratos`](https://github.com/ory/kratos) | `master` | Apache-2.0 | 2026-06-19T09:46:37Z | Identity/session self-service concepts; not ai-platform auth/session authority. |
| B1 memory/context | [`langchain-ai/langgraph`](https://github.com/langchain-ai/langgraph) | `main` | MIT | 2026-06-19T10:05:20Z | Checkpointing and state-graph vocabulary; not ai-platform memory policy. |
| B1 memory/context | [`mem0ai/mem0`](https://github.com/mem0ai/mem0) | `main` | Apache-2.0 | 2026-06-19T10:51:26Z | Memory update/delete UX and evaluation ideas; not tenant retention authority. |
| B1 memory/context | [`getzep/zep`](https://github.com/getzep/zep) | `main` | Apache-2.0 | 2026-06-19T00:24:23Z | Conversation memory and temporal context patterns; not ai-platform storage schema. |
| B1 memory/context | [`getzep/graphiti`](https://github.com/getzep/graphiti) | `main` | Apache-2.0 | 2026-06-19T00:37:03Z | Temporal knowledge graph concepts; not default long-term memory enablement. |
| B2 sandbox | [`OpenHands/OpenHands`](https://github.com/OpenHands/OpenHands) | `main` | GitHub reports `Other` | 2026-06-19T11:20:37Z | AI development workspace and command/artifact UX concepts only unless license/provenance review approves code intake. |
| B2 sandbox | [`e2b-dev/E2B`](https://github.com/e2b-dev/E2B) | `main` | Apache-2.0 | 2026-06-18T22:24:34Z | Secure sandbox lifecycle, templates, command execution, and artifact-return patterns. |
| B2 sandbox | [`daytonaio/daytona`](https://github.com/daytonaio/daytona) | `main` | AGPL-3.0 | 2026-06-19T11:37:40Z | Elastic sandbox and workspace isolation concepts only unless a focused legal/provenance issue approves adaptation. |
| B2 sandbox | [`google/gvisor`](https://github.com/google/gvisor) | `master` | Apache-2.0 | 2026-06-19T00:31:22Z | Container isolation and syscall boundary concepts; not a runtime switch without architecture and 211 evidence. |
| B2 sandbox | [`kata-containers/kata-containers`](https://github.com/kata-containers/kata-containers) | `main` | Apache-2.0 | 2026-06-19T10:49:21Z | VM-backed container isolation concepts; not a provider requirement. |
| B2 sandbox | [`firecracker-microvm/firecracker`](https://github.com/firecracker-microvm/firecracker) | `main` | Apache-2.0 | 2026-06-19T10:28:59Z | MicroVM isolation and resource-boundary vocabulary; not direct ai-platform runtime authority. |
| B2 sandbox | [`anthropic-experimental/sandbox-runtime`](https://github.com/anthropic-experimental/sandbox-runtime) | `main` | Apache-2.0 | 2026-06-19T10:35:52Z | OS-level filesystem and network restriction vocabulary; not an execution-provider switch without architecture and runtime evidence. |
| B3 worker/model gateway | [`temporalio/sdk-python`](https://github.com/temporalio/sdk-python) | `main` | MIT | 2026-06-19T00:18:36Z | Durable workflow/retry vocabulary and test patterns; not a migration requirement. |
| B3 worker/model gateway | [`celery/celery`](https://github.com/celery/celery) | `main` | GitHub reports `Other` | 2026-06-18T22:12:40Z | Worker pool, task retry, and queue monitoring vocabulary only unless license/provenance review approves intake. |
| B3 worker/model gateway | [`Bogdanp/dramatiq`](https://github.com/Bogdanp/dramatiq) | `master` | LGPL-3.0 | 2026-06-17T11:17:38Z | Actor/queue concepts only unless license/provenance review approves adaptation. |
| B3 worker/model gateway | [`taskiq-python/taskiq`](https://github.com/taskiq-python/taskiq) | `master` | MIT | 2026-06-07T09:13:08Z | Async task broker vocabulary and test ideas. |
| B3 worker/model gateway | [`BerriAI/litellm`](https://github.com/BerriAI/litellm) | `litellm_internal_staging` | GitHub reports `Other` | 2026-06-19T06:29:19Z | Model gateway routing, rate limit, budget, fallback, token/cost concepts only unless license/provenance review approves intake. |
| B3 worker/model gateway | [`Portkey-AI/gateway`](https://github.com/Portkey-AI/gateway) | `main` | MIT | 2026-05-25T13:54:51Z | Gateway routing, retries, budgets, and provider policy vocabulary; not external cost authority. |
| B4 Skills management | [`backstage/backstage`](https://github.com/backstage/backstage) | `master` | Apache-2.0 | 2026-06-19T11:26:36Z | Catalog metadata and ownership vocabulary. |
| B4 Skills management | [`langgenius/dify`](https://github.com/langgenius/dify) | `main` | GitHub reports `Other` | 2026-06-19T10:18:23Z | App/agent workflow and marketplace UX concepts only. |
| B4 Skills management | [`open-webui/open-webui`](https://github.com/open-webui/open-webui) | `main` | GitHub reports `Other` | 2026-06-18T22:16:09Z | Tool/function discovery and admin UX concepts only. |
| B4 Skills management | [`danny-avila/LibreChat`](https://github.com/danny-avila/LibreChat) | `main` | MIT | 2026-06-19T02:54:44Z | Agents, tools, slash-command, chat UI vocabulary, and frontend UX reference. |
| B4 Skills management | [`Mintplex-Labs/anything-llm`](https://github.com/Mintplex-Labs/anything-llm) | `master` | MIT | 2026-06-19T01:13:32Z | Workspace/agent marketplace concepts and local-first agent UX vocabulary. |
| B5 files/tools/authz | [`openfga/openfga`](https://github.com/openfga/openfga) | `main` | Apache-2.0 | 2026-06-18T20:49:29Z | Relationship-based authorization vocabulary and deny-path matrix ideas. |
| B5 files/tools/authz | [`authzed/spicedb`](https://github.com/authzed/spicedb) | `main` | Apache-2.0 | 2026-06-18T23:34:14Z | Relationship graph authorization patterns; not ai-platform ACL authority. |
| B5 files/tools/authz | [`apache/casbin`](https://github.com/apache/casbin) | `master` | Apache-2.0 | 2026-06-16T13:54:37Z | RBAC/ABAC policy vocabulary and table-driven tests. |
| B5 files/tools/authz | [`open-policy-agent/opa`](https://github.com/open-policy-agent/opa) | `main` | Apache-2.0 | 2026-06-18T17:49:38Z | Policy bundle and decision-log concepts. |
| B5 files/tools/authz | [`IBM/mcp-context-forge`](https://github.com/IBM/mcp-context-forge) | `main` | Apache-2.0 | 2026-06-19T11:34:34Z | MCP gateway/catalog routing and policy vocabulary; not exact tool-decision authority. |
| B5 files/tools/authz | [`agentic-community/mcp-gateway-registry`](https://github.com/agentic-community/mcp-gateway-registry) | `main` | Apache-2.0 | 2026-06-19T05:47:42Z | Governed MCP registry, OAuth, tool discovery, and audit vocabulary; not ai-platform tool authority. |
| B5 files/tools/authz | [`dockersamples/labspace-mcp-gateway`](https://github.com/dockersamples/labspace-mcp-gateway) | `main` | Apache-2.0 | 2026-05-02T02:43:49Z | MCP gateway packaging and Docker-oriented examples; not a runtime dependency. |
| B5 files/tools/authz | [`goodatlas/mcp-supergateway`](https://github.com/goodatlas/mcp-supergateway) | `main` | MIT | 2026-02-27T06:44:06Z | stdio/SSE gateway vocabulary and protocol bridging ideas; not exact permission authority. |
| B6 observability/quality | [`langfuse/langfuse`](https://github.com/langfuse/langfuse) | `main` | GitHub reports `Other` | 2026-06-19T11:39:55Z | Trace/token/cost dashboard vocabulary only unless license/provenance review approves intake. |
| B6 observability/quality | [`Arize-ai/phoenix`](https://github.com/Arize-ai/phoenix) | `main` | GitHub reports `Other` | 2026-06-19T01:45:37Z | LLM observability and evaluation workflow concepts only unless license/provenance review approves intake. |
| B6 observability/quality | [`open-telemetry/opentelemetry-collector`](https://github.com/open-telemetry/opentelemetry-collector) | `main` | Apache-2.0 | 2026-06-18T18:02:01Z | Telemetry collector/export vocabulary; not release-evidence authority. |
| B6 observability/quality | [`promptfoo/promptfoo`](https://github.com/promptfoo/promptfoo) | `main` | MIT | 2026-06-19T03:48:04Z | Golden-set and prompt regression workflow concepts. |
| B6 observability/quality | [`vibrantlabsai/ragas`](https://github.com/vibrantlabsai/ragas) | `main` | Apache-2.0 | 2026-02-24T07:47:19Z | RAG/evaluation metrics concepts; GitHub currently resolves the former `explodinggradients/ragas` reference here. |
| B6 observability/quality | [`Giskard-AI/giskard-oss`](https://github.com/Giskard-AI/giskard-oss) | `main` | Apache-2.0 | 2026-06-19T10:47:55Z | Model quality, evaluation, and testing workflow concepts. |

Additional unpinned project names remain concept-only until a future issue
records repository, commit/tag, license posture, and intake level. Code
adaptation still requires a focused issue with repository, commit/tag, license,
tests, and runtime evidence where applicable.

### 7.4 Reference Reading Order

Near-term reference reading should follow the backend risk order:

1. B2 sandbox: OpenHands, E2B, Daytona, gVisor, Kata Containers, Firecracker,
   and Anthropic Sandbox Runtime.
2. B3 capacity/model gateway: LiteLLM, Portkey, Temporal, Celery, Dramatiq, and
   Taskiq.
3. B4 Skills management: Backstage, Dify, Open WebUI, LibreChat, and AnythingLLM.
4. B1 memory/context: LangGraph, Mem0, Zep, and Graphiti.
5. B5/B6 policy and observability: OpenFGA, SpiceDB, Casbin, OPA,
   ContextForge MCP Gateway, MCP Gateway Registry, labspace MCP Gateway,
   mcp-supergateway, Langfuse, Phoenix, OpenTelemetry, promptfoo, Ragas, and
   Giskard.

Reading a project does not authorize copying code, adding dependencies, or
changing runtime architecture. Any code adaptation or runtime dependency must go
through issue, license/provenance review, tests, PR review, and runtime evidence
when applicable.

Repositories with GitHub license posture `Other`, AGPL/LGPL/copyleft terms, or
unknown license posture are concept-only references by default. They may inform
terminology, workflow shape, test-matrix ideas, and UX comparison, but not code
copying, vendoring, dependency addition, or runtime service introduction without
a separate issue that records commit/tag, files, license review, security review,
rollback, tests, and runtime evidence where applicable.

### 7.5 Supply-Chain Rule For References

Reference projects that handle credentials, model keys, runtime execution,
container launch, filesystem access, network egress, or package installation
require elevated supply-chain intake.

- Pin a reviewed commit or release tag before copying code, adding a dependency,
  or deploying an image.
- Record repository owner/name, URL, license posture, provenance, files copied or
  adapted, and the exact concept borrowed.
- Generate or review SBOM/dependency metadata when a runtime dependency,
  package, image, or sidecar is proposed.
- Check current security advisories, compromised package versions, and upstream
  maintainer guidance before adding any dependency to the worker, sandbox, model
  gateway, or release pipeline.
- Keep runtime dependency proposals out of implementation PRs until the
  architecture issue, rollback path, and 211 smoke plan are approved.

LiteLLM is a useful model-gateway reference, but it is security-sensitive
because LiteLLM's
[2026-03-24 security update](https://docs.litellm.ai/blog/security-update-march-2026)
identified compromised PyPI versions `1.82.7` and `1.82.8`. Any future LiteLLM
dependency proposal must pin a reviewed safe version or official image, record
provenance, run dependency/security review, and pass the elevated intake above.

## 8. Immediate Issue Chain

The next backend work should stay evidence-first. B0 is now a freshness watch
item after #164; do not spend the next active implementation slice on B0 unless
readiness reports runtime rollout, source/runtime drift, or a runtime-affecting
merge.

| Order | Issue theme | First PR goal | Why first |
| --- | --- | --- | --- |
| 0 | B0 freshness watch | Re-run B0 only when readiness reports runtime rollout, source/runtime drift, or a runtime-affecting merge. | Keeps #164 runtime-subject evidence from being reused after it becomes stale. |
| 1 | B1 governed document context-pack workflow | Select one document workflow and prove session/workspace context-pack policy, provenance, redaction, export/delete boundary, and rollback with `live_worker_run_payload` evidence. | Memory becomes useful only when tied to a workflow and deny paths; long-term memory remains fail-closed. |
| 2 | B2 real sandbox smoke through SDK Skill path | Turn sandbox contracts into Docker/equivalent `controlled_live_probe` and then `live_worker_run_payload` evidence for a governed Skill path. | `fake` and standalone verifier success remain the most visible blockers to real tool/Skill execution credibility. |
| 3 | B3 capacity profile | Produce an `operator_reviewed_recorded_snapshot` for 10 sessions x peak 4 SDK subagents/session without raising defaults. | SDK subagent capability exists; load, gateway, sandbox, event/artifact, and cost pressure remain unproven. |
| 4 | B4 Skill lifecycle slice | Make upload/version/release/rollback and dependency review operational enough for reviewed immutable Skill runs. | Skills are the user-facing product surface; mutable folders are not product governance. |
| 5 | B5 file/artifact plus exact-tool deny paths | Prove B5a file/artifact ACL and B5b exact shell/MCP/filesystem permission replay denial separately. | File-heavy workflows are high-value and high-leakage-risk; file ACL does not prove tool safety. |
| 6 | B6 named operations beta package | Bind Admin Runtime, trace/export, alert, golden-set, rollback, B1-B5 evidence, and owner signoff to one named workflow. | Product beta needs supportability evidence, not just successful generated files. |

## 9. Backend Product Beta Definition Of Done

The backend is product-beta ready only when one or two named internal workflows
have all of the following:

1. A named workflow owner, business owner, support owner, tenant/workspace
   scope, SLO, expected SDK subagent fanout, cost budget, alert route, and
   rollback drill.
2. Company login and same-tenant admin/ordinary-user behavior.
3. Governed Skill selection and immutable run snapshots.
4. Memory/context policy with provenance, export, delete/redaction, opt-out, and
   long-term fail-closed behavior unless explicitly proven otherwise.
5. Real sandbox evidence when workflow executes shell/script/code or other
   high-risk tools.
6. File upload, artifact preview/download, and unauthorized denial evidence.
7. Exact tool-permission decisions for shell, network, MCP, filesystem, and
   write-capable operations.
8. Worker/model-gateway capacity profile for the workflow, including token/cost
   accounting, event/artifact volume, and rollback.
9. Admin Runtime visibility into queue, worker, sandbox, model gateway,
   token/cost, latency, errors, dead letters, and release evidence.
10. Linked issue/PR/review/merge/211 evidence for every runtime claim.
11. Operator-reviewed recorded snapshots where capacity, hardening, or beta
    readiness is claimed.

Until these are true, a successful Skill-generated file is a controlled workflow
smoke, not product beta completion.
