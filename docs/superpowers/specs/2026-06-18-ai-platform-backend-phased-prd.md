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
observability are required for the P0 items to be safely exposed.

## 1. Authority And Status Language

This PRD narrows backend execution of the active product PRD:
`docs/superpowers/specs/2026-06-10-ai-platform-product-prd-v2.md`.

Use these sources together:

| Source | Responsibility |
| --- | --- |
| Product PRD v2 | Product goal, global reference boundaries, S1 historical baseline, G0-G10 roadmap. |
| This backend PRD | Backend B0-B6 sequencing, entry/exit gates, evidence boundaries, reference-code intake rules. |
| Technical acceptance matrix | Module-level acceptance text and regression expectations. |
| Gate status snapshot | Current status and caveats; never gate closure by itself. |
| Release evidence records | Reviewed runtime evidence, smoke output, source/runtime relation, image labels, redacted proof artifacts. |
| Guardrails and GitHub workflow | Implementation rules, issue/PR/review/merge/211 closure loop. |

If the documents disagree, stop feature work and repair source authority first.

All reports must use the narrowest true status:

| Status | Meaning | Minimum evidence |
| --- | --- | --- |
| `local partial` | Local docs, code, tests, or static evidence exist; no merge/runtime claim. | Targeted local verification or readiness output for the changed scope. |
| `PR ready` | A scoped PR is ready for review. | Issue link, branch, PR, changed-scope verification, and explicit boundary statement. |
| `reviewed` | Independent review was recorded and findings were resolved or tracked. | Review comment, approval, or evidence-backed review disposition. |
| `merged` | The PR landed on `main`. | GitHub merged state or merge commit. |
| `211 verified` | Runtime evidence exists for the named source/runtime subject. | Source/runtime relation, image labels, container health, smoke output, redaction scan, and reviewed evidence. |
| `gate closable` | The stage/gate issue can close. | Issue, PR, review, verification, docs, runtime evidence where required, and residual caveats. |

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

## 2. Non-Goals

| Non-goal | Boundary |
| --- | --- |
| Replacing ai-platform with Dify, Open WebUI, LibreChat, LangGraph, Temporal, or any other backend | External projects are references only. ai-platform owns identity, tenancy, RBAC, audit, source authority, release evidence, and gate closure. |
| Building a separate platform-level multi-run agent harness now | SDK Agent/subagent behavior remains execution-kernel fanout inside one governed platform run. Platform-level parent/child run orchestration is deferred. |
| Raising worker/model defaults by configuration only | B3 must prove the selected profile with load, backpressure, token/cost, sandbox pressure, cleanup, and rollback evidence. |
| Treating `fake` sandbox as production evidence | `fake` is only local/test proof. B2/G7 requires Docker/equivalent evidence on a Docker-capable host. |
| Enabling long-term memory by default | Long-term memory remains fail-closed until policy, retention, export, delete/redaction, opt-out, and tenant deny paths are proven. |
| Treating mutable Skill folders as production authority | Production Skill execution requires immutable versions, release state, dependency evidence, pinned snapshots, and used-skill audit. |

## 3. Backend Stage Model

B0-B6 are planning and acceptance bundles. They do not replace G0-G10 gates.

| Stage | Name | Product target | Gate mapping | Default status before evidence |
| --- | --- | --- | --- | --- |
| B0 | Latest-main backend readiness refresh | Current source, 211 source, deploy composition, runtime labels, backend/worker containers, and readiness tools agree. | G0-G1, S2-0 | `local partial` |
| B1 | Memory/context usable | Selected workflows can use governed memory/context with provenance, policy, and deny paths. | G6, G9 | `local partial` |
| B2 | Real sandbox usable | Docker/equivalent sandbox runs governed SDK Skill tasks with leases, quotas, egress policy, callback validation, cleanup, and artifact return. | G7, G6, G9 | `local partial` |
| B3 | Worker/model-gateway capacity | Target profile is measured before defaults increase: initial profile is 10 sessions x peak 4 SDK subagents/session. | G5, G8, G9 | `local partial` |
| B4 | Skills management and release governance | Skills can be uploaded/imported, versioned, reviewed, released, rolled back, pinned, dependency-reviewed, and audited. | G6, G9 | `local partial` |
| B5 | Files/artifacts/tool permission governance | File upload, artifact ACL, preview/download, exact tool approvals, MCP/shell/filesystem policy, and replay denial are proven. | G6, G7, G9 | `local partial` |
| B6 | Operations beta and workflow readiness | Admin Runtime, trace/export, alerting, golden-set eval, workflow owner signoff, rollback, and support model exist. | G9, G10 | `local partial` |

## 4. Stage Gates And Acceptance Boundaries

Each stage has four boundaries:

- Entry gate: when the team is allowed to start implementation.
- Local acceptance: what a PR must prove before review.
- Runtime acceptance: what 211 or another named Docker-capable target must
  prove when the stage touches runtime behavior.
- Exit gate: what must be true before the stage is `gate closable`.

### 4.0 Required Evidence Shape

Every backend stage issue or closure comment should use a consistent evidence
shape. The goal is to make the claim level auditable without reading chat
history.

| Field | Required content |
| --- | --- |
| `issue_or_decision` | GitHub issue, no-code decision, or direct-main exception that authorizes the slice. |
| `source_subject` | Commit, branch, source tree, runtime subject, and whether the claim is exact-current-source or runtime-relevant-source only. |
| `local_verification` | Targeted commands, tests, static verifiers, and known local blockers. |
| `runtime_verification` | 211 or named-target smoke, image/container identity, source/runtime relation, redaction result, and captured artifacts. |
| `review_disposition` | Independent review status, unresolved findings, rejected findings with evidence, and follow-up issues. |
| `residual_caveats` | Non-closing caveats such as deployment-layout/env-file drift, runtime-only rebase workaround, or partial visual acceptance. |
| `non_expansion_invariants` | Explicit booleans that prevent a narrow smoke from being read as broader production approval. |
| `rollback_or_disable_path` | Operator action that restores the prior runtime/configuration or disables the capability. |

Use these default `non_expansion_invariants` unless a later issue explicitly
changes and proves them:

- `production_concurrency_defaults_raised=false`
- `ordinary_user_platform_multi_run_orchestration_enabled=false`
- `docker_sandbox_hardening_claimed=false`
- `long_term_cross_session_memory_default_enabled=false`
- `department_rollout_allowed=false`

### 4.1 B0 Latest-Main Backend Readiness Refresh

**Entry gate**

- Latest `main` and current runtime subject are identified.
- 211 backend source path, repo-local deploy composition path, API/worker
  container names, image tag, and readiness command are known.
- An issue exists when runtime evidence is required.

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

### 4.2 B1 Memory And Context Usable

**Entry gate**

- A concrete workflow is selected.
- Memory scope is named: session, project/workspace, or policy-bound long-term.
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

**Runtime acceptance**

- One 211 workflow smoke proves memory/context is used by a governed run.
- Evidence records context provenance, selected policy, redaction result,
  artifact/event relation, and rollback or cleanup path.

**Exit gate**

- Memory is enabled only for the selected governed scope.
- Long-term memory, if still not fully proven, remains disabled by default.
- The issue records owner-facing behavior and operator rollback.

**Cannot claim**

- A memory table or route alone does not make memory usable.
- Session memory smoke does not prove long-term memory.

### 4.3 B2 Real Sandbox Usable

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

- 211 Docker/equivalent smoke proves launch, command execution, artifact return,
  cancel, cleanup, orphan scan, and no private projection leak.
- Evidence includes image/container identity, sandbox profile, resource limit,
  egress posture, callback validation, and cleanup result.

**Exit gate**

- `fake` is not used as production proof.
- The real provider has fail-closed behavior and rollback instructions.
- Operators can see sandbox lease state and failures through Admin Runtime or
  release evidence.

**Cannot claim**

- A successful SDK task in the worker process is not sandbox proof.
- Docker provider source code without 211 smoke is not production-ready sandbox.

### 4.4 B3 Worker And Model-Gateway Capacity

**Entry gate**

- Target profile is named. Initial profile:
  - 10 concurrent user sessions.
  - Peak 4 Claude Agent SDK subagents per session.
  - Selected workflows only.
- Existing production defaults remain unchanged until exit evidence is reviewed.

**Local acceptance**

- Load harness or bounded smoke tool records queue depth, lease latency, worker
  heartbeat, active-run admission, retry/cancel/dead-letter behavior,
  model-gateway timeout/backpressure, token/cost accounting, event/artifact
  volume, and cleanup.
- Tests prove tenant-aware quota/backpressure, user-aware limits, bounded queue
  metadata, and fail-closed model-gateway policy.

**Runtime acceptance**

- 211 or named target records the seven evidence gates:
  1. API burst behavior.
  2. Run creation burst behavior.
  3. Queue depth and lease latency.
  4. Worker start/heartbeat/terminal behavior.
  5. Model-gateway timeout, retry/backoff, provider-limit, and backpressure.
  6. Sandbox/container pressure when workflow uses sandbox.
  7. Cleanup, dead-letter, event/artifact volume, token/cost, and rollback.

**Exit gate**

- Operator-reviewed evidence proves the selected profile.
- Defaults are raised only by a separate explicit change after evidence review.
- Rollback restores previous worker/model/sandbox profile.

**Cannot claim**

- More worker processes or higher env values are not capacity proof.
- One fast run does not prove SDK subagent fanout pressure.

### 4.5 B4 Skills Management And Release Governance

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

**Exit gate**

- Skills have reviewed SBOM/license/vulnerability/dependency evidence or an
  explicit gate-limited exception.
- Ordinary users see only Skills they are authorized to use.
- Admins can inspect release state, dependency state, used-skill evidence, and
  rollback history without private executor payload leaks.

**Cannot claim**

- Copying a Skill directory into an image is not Skills management.
- A successful ad hoc Skill run does not prove release governance.

### 4.6 B5 Files, Artifacts, And Tool Permission Governance

**Entry gate**

- A high-risk workflow family is selected.
- File namespace, artifact ACL, preview/download, retention, exact tool
  decision, replay denial, MCP/shell/filesystem policy, and redaction rules are
  specified.

**Local acceptance**

- Tests cover file upload namespace, artifact owner/tenant ACL, preview/download
  authorization, cross-user and cross-tenant denial, exact permission binding to
  `tool_call_id` or stable request fingerprint, replay denial, and redaction.

**Runtime acceptance**

- 211 smoke proves file upload -> governed run -> artifact preview/download ->
  unauthorized denial for the selected workflow.
- Evidence records principal, tenant/workspace/user/run/file/artifact relation,
  permission decisions, and redaction outcome.

**Exit gate**

- Ordinary-user file workflows cannot bypass backend ACL, exact tool
  permission, or redaction controls.
- Admin evidence remains projection-safe and does not reveal executor private
  payloads.

**Cannot claim**

- Admin download success does not prove ordinary-user ACL safety.
- Broad "latest allow" decisions do not satisfy exact tool approval.

### 4.7 B6 Operations Beta And Department Workflow Readiness

**Entry gate**

- One operations package or department workflow package has owner, SLO, capacity
  profile, cost budget, quality gate, alert, trace/export, rollback, and support
  requirements.

**Local acceptance**

- Tests or static verifiers cover Admin Runtime views, trace/export, alert
  policy, golden-set eval packaging, release-evidence export, owner metadata,
  rollback metadata, and ordinary-user denial.

**Runtime acceptance**

- 211 evidence proves Admin Runtime, trace/export, alert calibration,
  golden-set evaluation, workflow smoke, rollback drill, and owner signoff for
  the selected workflow.

**Exit gate**

- A named workflow owner accepts capacity, quality, audit, cost, support, and
  rollback evidence.
- Operators can diagnose queue, worker, sandbox, model gateway, cost, latency,
  errors, dead letters, and release evidence without reading chat transcripts.

**Cannot claim**

- A generated final document is workflow success, not operations beta.
- Observability screenshots without redacted evidence and owner signoff are not
  gate closure.

## 5. Universal Gate Closure Checklist

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

## 6. Reference Code Projects

Reference projects can shape implementation choices, tests, and UI vocabulary.
They do not define ai-platform authority. Every use of reference code must be
classified before implementation.

### 6.1 Intake Levels

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

### 6.2 Reference Matrix By Backend Stage

| Stage | Reference projects | Useful for | Not allowed to define |
| --- | --- | --- | --- |
| B0 source/auth baseline | Keycloak, Authentik, Ory Kratos | OIDC/session, group/role mapping, admin login, enterprise identity integration vocabulary. | ai-platform tenant mapping, RBAC enforcement, audit, source authority. |
| B1 memory/context | LangGraph, Mem0, Zep, Graphiti | Checkpointing, short-term vs long-term memory, memory update/delete UX, temporal/provenance concepts. | `memory_records`, context snapshots, tenant policy, retention, delete/redaction authority. |
| B2 sandbox | OpenHands, E2B, Daytona, Anthropic Sandbox Runtime/SRT concept notes | Workspace isolation, sandbox lifecycle, command execution, artifact return, cancel, cleanup, callback/egress patterns. | Docker socket trust, external SaaS authority, bypassing ai-platform lease/audit/sandbox policy. |
| B3 worker/model gateway | Temporal Python SDK, Celery, Dramatiq, Taskiq, LiteLLM, Portkey | Durable retry vocabulary, worker scaling, gateway routing, rate limits, budgets, provider fallback, token/cost concepts. | Replacing current queue/worker/model policy without a migration gate; external cost authority. |
| B4 Skills management | Backstage, Dify, Open WebUI, LibreChat, AnythingLLM | Catalog metadata, release workflow, app/agent marketplace UX, slash/tool discovery patterns. | Skill execution policy, tenant visibility, release authority, backend runtime lifecycle. |
| B5 files/tools/authz | OpenFGA, SpiceDB, Casbin, Open Policy Agent, ContextForge MCP Gateway, supergateway | Relationship-based ACLs, RBAC/ABAC policy tests, policy bundles, MCP/tool catalog routing, decision logs. | Backend ACL enforcement, exact tool decisions, audit, tenant/run/file/artifact authority. |
| B6 observability/quality | Langfuse, Phoenix, OpenTelemetry Collector, promptfoo, Ragas, Giskard | Trace/span vocabulary, token/cost dashboards, eval runs, golden-set regression, telemetry export, redaction review. | Release evidence authority, private payload storage, workflow-owner acceptance thresholds. |

### 6.3 Confirmed Repository References

The following repository names were checked for this PRD update and are allowed
as confirmed repository references for planning and comparison. They are not
approved dependencies.

| Area | Repository reference | Use only for |
| --- | --- | --- |
| B1 memory/context | `langchain-ai/langgraph`, `mem0ai/mem0`, `getzep/zep`, `getzep/graphiti` | Memory/checkpointing vocabulary, provenance ideas, retention/delete UX, and test-matrix patterns. |
| B2 sandbox | `OpenHands/OpenHands`, `e2b-dev/E2B`, `daytonaio/daytona` | Sandbox lifecycle, workspace isolation, command execution, artifact return, cancel, cleanup, and cloud/local sandbox comparison. |
| B3 worker/model gateway | `temporalio/sdk-python`, `celery/celery`, `Bogdanp/dramatiq`, `taskiq-python/taskiq`, `BerriAI/litellm` | Durable task vocabulary, queue/worker retry patterns, model gateway routing, rate limits, budgets, fallback, and token/cost concepts. |
| B4 Skills management | `backstage/backstage`, `langgenius/dify`, `open-webui/open-webui`, `danny-avila/LibreChat`, `Mintplex-Labs/anything-llm` | Catalog metadata, marketplace UX, agent/app discovery, Skill release workflow vocabulary, slash-command/tool discovery patterns. |

Portkey, Keycloak, Authentik, Ory Kratos, OpenFGA, SpiceDB, Casbin, Open Policy
Agent, ContextForge MCP Gateway, supergateway, Langfuse, Phoenix,
OpenTelemetry Collector, promptfoo, Ragas, and Giskard remain valid reference
targets in the matrix above, but code adaptation still requires a focused issue
with repository, commit/tag, license, tests, and runtime evidence where
applicable.

### 6.4 Reference Reading Order

Near-term reference reading should follow the backend risk order:

1. B2 sandbox: OpenHands, E2B, Daytona, and Anthropic Sandbox Runtime/SRT
   concept notes.
2. B3 capacity/model gateway: LiteLLM, Portkey, Temporal, Celery, Dramatiq, and
   Taskiq.
3. B4 Skills management: Backstage, Dify, Open WebUI, LibreChat, and AnythingLLM.
4. B1 memory/context: LangGraph, Mem0, Zep, and Graphiti.
5. B5/B6 policy and observability: OpenFGA, SpiceDB, Casbin, OPA,
   ContextForge MCP Gateway, Langfuse, Phoenix, OpenTelemetry, promptfoo,
   Ragas, and Giskard.

Reading a project does not authorize copying code, adding dependencies, or
changing runtime architecture. Any code adaptation or runtime dependency must go
through issue, license/provenance review, tests, PR review, and runtime evidence
when applicable.

## 7. Immediate Issue Chain

The next backend work should stay evidence-first:

| Order | Issue theme | First PR goal | Why first |
| --- | --- | --- | --- |
| 1 | B0 latest-main evidence refresh | Close only the current-source readiness gap that the evidence actually proves. | Prevents historical S1 evidence from being mistaken for latest-main readiness. |
| 2 | B1 memory/context workflow smoke | Select one document workflow and prove policy, provenance, redaction, and rollback. | Memory becomes useful only when tied to a workflow and deny paths. |
| 3 | B2 real sandbox smoke | Turn sandbox contracts into Docker/equivalent 211 evidence. | `fake` remains the most visible blocker to real tool/Skill execution credibility. |
| 4 | B3 capacity profile | Measure 10 sessions x peak 4 SDK subagents without raising defaults. | SDK subagent capability exists; load, gateway, sandbox, event/artifact, and cost pressure remain unproven. |
| 5 | B4 Skill lifecycle slice | Make upload/version/release/rollback and dependency review operational enough for reviewed Skill runs. | Skills are the user-facing product surface; mutable folders are not product governance. |
| 6 | B5 file/artifact/tool deny paths | Prove exact permission, file ACL, artifact preview/download, and replay denial for document workflows. | File-heavy workflows are high-value and high-leakage-risk. |
| 7 | B6 operations beta package | Add Admin Runtime, trace/export, alert, golden-set, rollback, and owner signoff for one named workflow. | Product beta needs supportability evidence, not just successful generated files. |

## 8. Backend Product Beta Definition Of Done

The backend is product-beta ready only when one or two named internal workflows
have all of the following:

1. Company login and same-tenant admin/ordinary-user behavior.
2. Governed Skill selection and immutable run snapshots.
3. Memory/context policy with provenance, export, delete/redaction, opt-out, and
   long-term fail-closed behavior unless explicitly proven otherwise.
4. Real sandbox evidence when workflow executes shell/script/code or other
   high-risk tools.
5. File upload, artifact preview/download, and unauthorized denial evidence.
6. Exact tool-permission decisions for shell, network, MCP, filesystem, and
   write-capable operations.
7. Worker/model-gateway capacity profile for the workflow, including token/cost
   accounting and rollback.
8. Admin Runtime visibility into queue, worker, sandbox, model gateway,
   token/cost, latency, errors, dead letters, and release evidence.
9. Workflow owner signoff, quality threshold, cost budget, alert rules, support
   owner, and rollback drill.
10. Linked issue/PR/review/merge/211 evidence for every runtime claim.

Until these are true, a successful Skill-generated file is a controlled workflow
smoke, not product beta completion.
