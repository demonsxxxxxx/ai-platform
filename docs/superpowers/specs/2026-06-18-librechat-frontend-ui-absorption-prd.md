# LibreChat Frontend UI Absorption PRD

> Status: proposed companion PRD for S2 frontend direction.
>
> Scope: define the product goal, adoption boundary, migration slices, and
> acceptance criteria for making ai-platform's primary frontend experience use
> LibreChat-style frontend UI while keeping the ai-platform backend, APIs,
> identity, tenancy, run ledger, skill governance, artifact ACL, memory policy,
> and audit model as the only product authority.
>
> This document is not implementation evidence, gate closure evidence, or a
> packaged frontend release claim. It must be implemented through the normal
> issue -> branch/PR -> review -> merge -> 211 smoke workflow where required.

## 1. Product Decision

ai-platform should absorb LibreChat's frontend UI and interaction model as the
primary chat-first product experience, but it must not adopt LibreChat's backend
or product authority model.

The intended outcome is a frontend that feels like LibreChat for daily users:
fast chat navigation, polished composer behavior, skill/agent selection from the
input surface, artifact side panels, file-aware conversations, responsive layout,
and admin-friendly management surfaces. The authoritative backend remains
ai-platform: all data shown in the UI must come from ai-platform public/admin
projections and all actions must go through ai-platform API contracts.

The program is intentionally phased. Phase 1 is an existing-interface
absorption slice: adopt the LibreChat-style shell, composer, run/artifact
surfaces, and route/RBAC model while wiring as much of the UI as possible to
current ai-platform routes. Backend contracts that do not exist today must not
be faked as production features in Phase 1; they move to Phase 2 as explicit
backend-plus-frontend work.

## 2. Goals

| Goal | Target outcome |
| --- | --- |
| Existing-interface-first migration | Phase 1 connects migrated UI surfaces to current ai-platform auth, chat/session, run, event, artifact, admin runtime, skill governance, tool policy, and memory projections wherever those contracts already exist. |
| RBAC replacement | Phase 1 replaces imported LibreChat/LambChat role assumptions with ai-platform principal, role, permission, tenant, and admin gating semantics across navigation, route guards, action buttons, and empty/denied states. |
| Chat-first shell | Replace the current mixed shell experience with a LibreChat-style chat workspace: conversation sidebar, main thread, collapsible detail panels, and dense but readable navigation. |
| Skill-first user loop | Users can discover, select, and invoke platform Skills from the chat input and related panels using a LibreChat-like command/selector experience. |
| Agent/task ergonomics | Users can select platform-approved agents, models, files, context, and tool modes through familiar controls without exposing backend-private state. |
| Artifact and run visibility | Generated files, previews, tool calls, permission requests, run playback, and context provenance appear in polished side panels or inline blocks modeled after LibreChat-style artifacts and tool UX. |
| Admin continuity | Admin Runtime, Skills, Marketplace, MCP, users, roles, models, memory, and governance pages remain reachable inside the new shell instead of being dropped during UI adoption. |
| Source authority preservation | Frontend UI changes do not alter backend authority. ai-platform owns auth/session, tenant/workspace/user/session/run IDs, queue admission, sandbox policy, skill snapshots, events, artifacts, memory, audit, cost, and release evidence. |

## 3. Non-Goals

| Non-goal | Reason |
| --- | --- |
| Replacing the ai-platform backend with LibreChat | ai-platform is an enterprise control plane. LibreChat backend APIs, database models, ACL, agent execution, and persistence are not the product source of truth. |
| Copying LibreChat as a separate app beside ai-platform | A second frontend authority would split auth, session, routes, artifact access, and evidence. The target is one ai-platform frontend surface. |
| Importing LibreChat data-provider contracts as-is | Those contracts assume LibreChat's backend. ai-platform frontend must use ai-platform service adapters and public/admin projections. |
| Implementing missing backend product contracts in Phase 1 | Phase 1 is a frontend absorption and RBAC replacement slice over existing contracts. Missing department marketplace, MCP management, user/role CRUD, model administration, notification, or settings contracts require Phase 2 backend issues. |
| Closing packaged frontend release acceptance from this PRD alone | Closure requires implementation, build evidence, projection audit, review, and 211 smoke where applicable. |
| Broadening ordinary-user write tools or SDK subagent exposure | UI adoption cannot bypass G5/G6/G7/G8/G9 gates, capacity evidence, or tool permission controls. |

## 4. Reference Source Boundary

LibreChat is a frontend UI and interaction reference for this PRD. Before
copying non-trivial code, the implementation issue must pin a LibreChat source
commit and record the reviewed license/provenance note. The current public
source has React/Vite/Tailwind frontend code and a root repository license file,
while package metadata may not be identical across packages; copying code
requires the implementation PR to record the exact source paths and license
handling.

| Source | Absorb | Do not absorb |
| --- | --- | --- |
| LibreChat frontend | Layout patterns, chat sidebar, composer behavior, command palette, skill picker, agent selector, artifact panels, file interaction UX, responsive behavior, selected pure UI components after dependency review. | LibreChat backend routes, Mongo data model, user/session/auth authority, ACL as product truth, agent execution model, RAG/file stores, provider secrets, rate limits, or admin policy authority. |
| ai-platform frontend | Existing service adapters, auth/session hooks, projection audit rules, permission cards, run playback, artifact preview allowlists, skill/marketplace/MCP/admin runtime pages. | Legacy UI structure where it blocks LibreChat-style shell adoption. |
| ai-platform backend | All authoritative API contracts and public/admin projection shapes. | UI-only shortcuts that read private runtime paths, raw payloads, secrets, storage keys, or executor-private state. |

## 5. Technical Compatibility Judgment

The two frontends are compatible enough for UI absorption but not compatible
enough for a direct drop-in replacement.

| Area | ai-platform current frontend | LibreChat frontend | Adoption rule |
| --- | --- | --- | --- |
| Framework | React 19, Vite 6, TypeScript, Tailwind, pnpm | React 18, Vite 8, TypeScript, Tailwind, npm workspaces/turbo | Port UI patterns and components deliberately; do not copy package structure wholesale. |
| Routing | React Router 7 | React Router 6 | Recreate routes in ai-platform routing instead of importing LibreChat route trees unchanged. |
| State/API | ai-platform service adapters and public/admin projections | LibreChat client packages and backend-specific API calls | Replace LibreChat API hooks with ai-platform adapters before merging. |
| Streaming | ai-platform SSE/event projection handling | LibreChat streaming helpers and resumable stream UX | Reuse UX concepts; preserve ai-platform event taxonomy and redaction. |
| UI libraries | Tailwind, lucide, CodeMirror, Sandpack, Mermaid, document preview libs | Tailwind, lucide, Radix/Ariakit/Headless UI/TanStack, Sandpack, Mermaid | Add dependencies only when a migrated component justifies them and build size/accessibility remain acceptable. |

## 6. Target User Experience

### 6.1 Ordinary User Surface

The first screen after login should be the chat/task workspace, not a marketing
page or a fragmented admin menu. The workspace should include:

- Conversation/session sidebar with search, recent runs, pinned workflows, and
  visible tenant/workspace scope.
- Main chat thread with streaming run events, tool call blocks, permission
  requests, artifacts, file previews, token/cost summaries where allowed, and
  failure states with recovery actions.
- Composer with attachments, skill selector, agent/model selector, context
  controls, and governed tool mode controls.
- Side panel for artifact preview, run playback, context provenance, selected
  files, and permission history.
- Responsive behavior suitable for desktop and laptop-first enterprise use,
  with mobile views remaining usable for monitoring and lightweight chat.

### 6.2 Skill And Marketplace Surface

Skills must feel native to the chat loop:

- Users can discover available Skills from the composer, a Skills hub, and a
  Marketplace surface.
- A selected Skill shows name, description, version/source, required inputs,
  allowed tools, expected artifacts, and permission risk before execution.
- Skill invocation records platform skill snapshot identity and used-skill
  evidence through ai-platform backend routes.
- Skill management remains admin/governance controlled; the UI must not allow
  hidden local or unreviewed global skill execution.

### 6.3 Admin And Governance Surface

Admin views must not be sacrificed to chat polish. The new shell must keep:

- Admin Runtime: queue depth, running runs, worker heartbeat, capacity,
  backpressure, sandbox state, model-gateway status, latency/error summaries.
- Skills governance: staged/released Skills, snapshot pinning, review evidence,
  SBOM/license/vulnerability status when available.
- Tool permission policy: allow/ask/deny settings, decision history, exact
  tool-call binding, and bulk review surfaces.
- Tenant/user/RBAC management with no raw private payload exposure.

## 7. Architecture

The frontend is a same-repository product surface under `frontend/web`. LibreChat
UI absorption should happen inside this frontend unless a future issue proves a
temporary migration worktree is safer. The production app must still build and
deploy as the ai-platform frontend.

```text
Browser UI
  -> LibreChat-style shell/components adapted into frontend/web
  -> ai-platform service adapters
  -> ai-platform public/admin API projections
  -> ai-platform backend control plane
  -> queue / worker / executor / sandbox / events / artifacts / audit
```

Implementation must preserve these boundaries:

- Auth uses ai-platform `/api/auth/*` contracts.
- Chat/session/run creation uses ai-platform run/session APIs.
- Streaming displays ai-platform run events and redacted projections.
- Artifacts use ai-platform artifact ACL, preview allowlists, and download
  routes.
- Skills use ai-platform skill snapshots, release state, and permission policy.
- Admin pages use ai-platform admin projections only.
- Frontend code must not read executor-private payloads, raw storage keys,
  sandbox work directories, command fingerprints, secret-like env values,
  provider keys, raw queue payloads, raw decision payloads, or hidden runtime
  metadata.

### 7.1 Phase 1 Existing Interface Alignment

Phase 1 should prefer adapters over backend expansion. The implementation issue
must include an interface map with each migrated surface classified as
`reuse-current`, `remap-current`, `fail-closed-placeholder`, or `phase-2-backend`.

| Surface | Phase 1 target | Existing route family to prefer | Phase 2 trigger |
| --- | --- | --- | --- |
| Auth/session/RBAC | Replace imported RBAC with ai-platform principal, roles, permissions, tenant, and admin checks. | `/api/auth/*`, `/api/ai/auth/*`, existing frontend auth hooks. | New department or role-management APIs beyond current principal projection. |
| Chat/session shell | Use current session list, message history, and run creation/streaming behavior. | `/api/ai/chat/sessions`, `/api/ai/chat/stream`, `/api/ai/runs`. | New conversation metadata, sharing, or team inbox features. |
| Composer Skill picker | Ordinary users select current public agent/capability projections and submit public `agent_id` values through chat; admin/governance views may inspect agent apps and governed Skills. Avoid raw skill IDs in ordinary-user UI or payload authority. | `/api/agents`, `/api/chat/stream`, `/api/ai/runs`; admin/governance only: `/api/ai/agent-apps`, `/api/ai/admin/skills/*`. | Department-specific Skill availability, marketplace install/purchase, or per-user pinning APIs. |
| Events/playback/artifacts | Render current public event, playback, artifact preview, and download projections. | `/api/ai/runs/{run_id}/events`, `/api/ai/runs/{run_id}/events/stream`, `/api/ai/runs/{run_id}/playback`, `/api/ai/artifacts/*`. | New artifact trees, richer provenance, or new event families. |
| Tool permission/MCP execution | Show existing permission cards and admin tool policy surfaces; no new ordinary-user write-tool exposure. | `/api/ai/runs/{run_id}/tool-permissions/*`, `/api/ai/admin/tool-policies*`. | MCP server/tool marketplace, department policy assignment, user-managed server lifecycle. |
| Skills governance | Keep admin Skill release, upload, promote, rollback, diff, and sync flows where already backed. | `/api/ai/admin/skills/*`. | Public department Skill marketplace, publish/install workflow, SBOM/license UX beyond current contracts. |
| Admin Runtime and memory | Integrate current admin runtime, queue, memory policy, records, and redaction projections into the shell. | `/api/ai/admin/runtime/*`, `/api/ai/memory/*`, `/api/ai/admin/memory/*`. | Cross-department dashboards, notification workflows, or advanced retention governance. |
| Users, roles, models, settings, notifications | Phase 1 must either remap to an existing ai-platform projection or show a permission-gated fail-closed placeholder with a Phase 2 issue link. | Existing active ai-platform projections only; legacy `/api/users`, `/api/roles`, `/api/settings`, `/api/agent/models/*`, `/api/notifications/*` are not product authority unless remapped. | Backend contracts for company users, department roles, model admin, notification, and settings management. |

Phase 1 route guards must fail closed. If a surface lacks a current
ai-platform public/admin projection, the new shell may keep the navigation
slot only when it displays a clear unavailable state for the user role and links
the implementation backlog; it must not call LibreChat/LambChat backend-style
endpoints as product truth.

## 8. Migration Slices

### 8.1 Phase 1: Existing-Interface UI Absorption

| Slice | Name | Outcome | Required evidence |
| --- | --- | --- | --- |
| FE-0 | Source, dependency, and interface audit | Pin LibreChat source commit, inventory candidate UI components, classify dependencies, and map every migrated surface to `reuse-current`, `remap-current`, `fail-closed-placeholder`, or `phase-2-backend`. | Issue with source paths, dependency table, license note, explicit rejected backend/data-provider imports, and an endpoint/interface matrix. |
| FE-1 | Shell, navigation, and RBAC replacement | LibreChat-style shell becomes the default authenticated layout; navigation and route guards use ai-platform principal roles/permissions and fail closed for unsupported surfaces. | Frontend build, route smoke, auth/RBAC smoke, screenshot or browser evidence, no route loss for current admin pages, no unauthorized ordinary-user route access. |
| FE-2 | Composer, Skills, agents, and existing run APIs | Chat input supports attachments, public capability selection, agent/model selection where already projected, and governed execution options using existing ai-platform APIs. Ordinary users use `/api/agents` and never treat persona `skill_names` or raw skill IDs as execution authority. | Unit/component tests for selectors, disabled/unavailable states, permission states, run creation, and projection audit. |
| FE-3 | Run events, permissions, artifacts, and playback | Streaming events, tool calls, permission cards, artifacts, previews, and run playback appear in the new UI without private leaks using current event/artifact/playback routes. | Event rendering tests, artifact URL safety tests, redaction tests, reconnect/replay checks, browser smoke. |
| FE-4 | Existing admin/governance remap | Admin Runtime, current Skills governance, tool policies, memory, model availability, active notifications, and any already-backed admin surfaces are integrated into the new shell; missing users/roles/MCP lifecycle/marketplace/settings CRUD/model CRUD/notification CRUD features are gated as Phase 2 placeholders. | Permission-gated route tests, admin smoke, projection audit, unavailable-state tests, no ordinary-user access regression. |
| FE-5 | Phase 1 packaged frontend delivery | The Phase 1 frontend builds into the packaged frontend image and serves through the 211 frontend entry with current backend contracts. | Docker-capable build/smoke evidence on 211 or approved host, same-commit label/provenance, release evidence file. |

### 8.2 Phase 2: Backend-Backed Product Expansion

| Slice | Name | Outcome | Required evidence |
| --- | --- | --- | --- |
| BE/FE-6 | Department Skill marketplace | Backend exposes tenant/department/role/user scoped Skill availability, install/enable/disable policy, audit, and rollback; frontend turns the Phase 1 placeholder into a real marketplace. | Backend route/schema tests, RBAC deny-path tests, frontend marketplace tests, projection audit, 211 smoke when runtime-affecting. |
| BE/FE-7 | MCP management and department policy | Backend exposes governed MCP server/tool inventory and policy assignment for departments without ordinary-user write-tool expansion; frontend integrates the management surface. | Tool-policy tests, exact permission binding tests, deny-path tests, frontend admin smoke, redacted audit evidence. |
| BE/FE-8 | Users, roles, models, settings, notifications | Backend defines company user/role/model/settings/notification public/admin projections; frontend replaces placeholders with real pages. | Contract tests for each projection, route guard tests, frontend happy/deny tests, projection audit. |

## 9. Acceptance Criteria

### 9.1 Product Acceptance

- Phase 1 ordinary users can login, start a chat/run, upload allowed files,
  select a currently public agent/capability from `/api/agents`, submit a task,
  watch streaming progress, approve or deny permission requests, and download
  authorized artifacts from the LibreChat-style shell.
- Phase 1 admin users can access current Admin Runtime, Skills governance, tool
  policies, memory, and any other already-backed ai-platform admin projections
  from the same shell.
- Phase 1 users/roles/MCP/Marketplace/settings/notifications/model surfaces
  are accepted only when they use an existing ai-platform public/admin
  projection or display a permission-gated Phase 2 unavailable state; they are
  not accepted when backed by LibreChat/LambChat data-provider contracts.
- Phase 2 completes department Skill marketplace, MCP lifecycle management,
  user/role, model administration, settings CRUD, and notification
  administration surfaces after backend contracts exist.
- The Skills experience is present in both chat input and dedicated management
  pages; users should not need to know backend route names to use Skills.
- Artifact preview and run playback remain first-class parts of the task
  experience, not separate debug-only pages.
- Error states identify whether the issue is auth, permission, upload, queue,
  model gateway, sandbox, skill validation, artifact ACL, or backend failure.

### 9.2 Security And Governance Acceptance

- Projection audit reports no active private-payload violations for the new
  browser graph.
- Ordinary-user frontend code cannot access raw runtime paths, raw storage keys,
  sandbox workdirs, private executor payloads, provider secrets, raw queue
  payloads, raw tool decision payloads, or raw Skill staging paths.
- All write/high-risk tool actions still require ai-platform permission policy
  and exact `tool_call_id` or stable request-fingerprint binding.
- Imported LibreChat/LambChat RBAC assumptions are removed from the active
  browser graph. Navigation, route loaders, action buttons, and empty states
  derive authority from ai-platform principal roles/permissions and same-tenant
  admin rules.
- Skill invocation records the ai-platform skill snapshot and rejects hash or
  release-state mismatches according to existing backend rules.
- Cross-tenant and cross-user artifact/session/file access remains denied in
  focused tests and 211 smoke when runtime evidence is required.

### 9.3 Technical Acceptance

- `frontend/web` remains the production frontend source and can run its
  install/lint/type/build/projection verification without introducing a second
  frontend product authority.
- LibreChat code copied into the repo is adapted to ai-platform services before
  merge; no imported component may call LibreChat backend endpoints directly.
- Phase 1 PRs include an endpoint/interface matrix proving each active call is
  backed by an existing ai-platform route, a remapped safe projection, or a
  fail-closed unavailable state.
- New dependencies are justified in the PR with purpose, bundle/runtime impact,
  license status, and a removal path if the component is later rewritten.
- Route guards replace legacy RBAC behavior with ai-platform principal and
  permission semantics for ordinary users and admins.
- Streaming event rendering remains compatible with ai-platform event taxonomy,
  redaction, replay/history loading, and run terminal states.
- Browser tests or component tests cover at least one happy path and one deny or
  failure path for migrated chat, skill, artifact, and admin surfaces.

### 9.4 UX Acceptance

- The authenticated app visually reads as one coherent LibreChat-style product,
  not a mixture of unrelated shells.
- Primary navigation is predictable: ordinary user task surfaces are prominent,
  admin/governance surfaces are available but permission-gated, and hidden pages
  are not required for normal workflows.
- The composer supports keyboard-first usage, file attachment, skill/agent/model
  selection, disabled/loading states, and clear recovery from failed uploads or
  blocked permissions.
- The UI meets enterprise dashboard quality: dense but scannable, restrained
  colors, stable spacing, no nested-card clutter, no decorative hero sections,
  no overlapping text, and accessible focus states.
- Mobile and narrow desktop layouts keep chat, composer, navigation, and side
  panels usable without horizontal overflow.

### 9.5 Evidence And Closure Acceptance

- Each implementation slice has a GitHub issue or linked parent issue, PR,
  review boundary, targeted verification, and explicit status label:
  `local partial`, `PR ready`, `merged`, `211 verified`, or `gate closable`.
- Docs-only PRD updates do not claim 211 verification.
- Browser-code changes must run frontend projection audit and focused frontend
  verification before PR readiness.
- Runtime-affecting frontend packaging changes require packaged frontend smoke
  on a Docker-capable host before release acceptance.
- The final migration is not gate-closable until the new UI is deployed on 211,
  auth/session works, ordinary and admin workflows smoke successfully, projection
  audit passes, and release evidence records the source/image/runtime relation.

## 10. Definition Of Done For The Frontend UI Absorption Program

Phase 1 is done only when:

1. The default authenticated ai-platform frontend shell uses the adopted
   LibreChat-style UI and no longer exposes the old shell as the primary user
   experience.
2. Ordinary users can complete the core chat -> Skill -> run -> permission ->
   artifact workflow through the new shell using current ai-platform routes.
3. Admin users can complete the current Admin Runtime -> Skill governance ->
   tool policy -> memory/governance inspection workflows through the new shell.
4. ai-platform backend projections remain the only data authority.
5. Projection audit, focused frontend tests, backend route/permission tests, and
   packaged frontend smoke have recorded evidence.
6. 211 evidence proves the deployed frontend, backend, worker, source labels,
   and release evidence all point to the same accepted source subject.
7. Missing users/roles/MCP lifecycle/Marketplace/settings CRUD/model CRUD and
   notification CRUD features are either safely hidden or represented as
   fail-closed Phase 2 placeholders, while existing model availability and
   active-notification projections remain usable in Phase 1.

The full program is done only when Phase 2 backend contracts also allow
department Skill marketplace, MCP management, user/role administration, model
administration, settings, and notifications to be real backed product surfaces.

## 11. Open Risks

| Risk | Mitigation |
| --- | --- |
| React/router version mismatch | Port components behind ai-platform wrappers; avoid importing LibreChat route trees unchanged. |
| Dependency growth | Add dependencies slice-by-slice with bundle/license review; prefer existing ai-platform dependencies when equivalent. |
| Backend contract leakage | Require adapter review and projection audit for every migrated component. |
| Admin pages become second-class | Treat admin/governance route coverage as FE-4 acceptance, not a follow-up polish item. |
| Phase 1 silently depends on missing backend work | Require the FE-0 endpoint/interface matrix and fail-closed placeholders for every surface without a current ai-platform projection. |
| RBAC drift from imported frontend assumptions | Replace active route guards and action gates with ai-platform principal/permission checks in FE-1, then cover ordinary/admin deny paths in tests. |
| Visual inconsistency | Establish a single LibreChat-style shell/design token layer before migrating deep pages. |
| License/provenance ambiguity | Pin source commit and record copied source paths, license note, and any required attribution in the implementation PR. |

## 12. Immediate Next Actions

1. Open a parent GitHub issue for `LibreChat frontend UI absorption` and a
   Phase 1 child issue for `existing-interface UI absorption + RBAC replacement`.
2. Pin a LibreChat source commit and create the FE-0 component/dependency
   inventory plus endpoint/interface matrix.
3. Make the first implementation PR migrate the authenticated shell, navigation,
   ai-platform principal/RBAC guards, and fail-closed unavailable-state pattern.
4. Put department Skill marketplace, MCP management, users/roles, models,
   settings, and notifications into explicit Phase 2 backend-backed issues
   instead of treating them as Phase 1 blockers.
5. Update the main PRD and technical acceptance matrix to reference this
   companion PRD after review.
