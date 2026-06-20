# AI Platform Chat Experience Parity PRD

> Status: proposed product-experience PRD for S2 frontend direction.
>
> Scope: define the user-facing chat workspace, composer, Skills, MCP, and
> marketplace experience that must replace the imported LambChat-style shell as
> the primary ai-platform frontend. This PRD supersedes visual and interaction
> acceptance from `2026-06-18-librechat-frontend-ui-absorption-prd.md`, while
> preserving that document's backend-authority, auth, RBAC, projection-audit,
> and deployment-evidence constraints.
>
> This document is not implementation evidence. It must be delivered through the
> normal issue -> branch/PR -> review -> merge -> 211 smoke workflow when code or
> deployment changes are involved.

## 1. Product Decision

ai-platform needs a company-internal AI workbench frontend, not a lightly
rebranded imported chat shell. The target experience is a chat-first workspace
inspired by the preferred reference frontend's product behavior, visual density,
and composer ergonomics, while all authority remains in ai-platform backend
contracts.

The current Phase 1A foundation is valuable because it proves the frontend can
serve through the official 211 entry and use ai-platform auth, RBAC, routes,
projection audit, and packaged build evidence. It is not acceptable as the final
user experience because the main shell still reads as the imported LambChat
surface and the composer does not provide the expected slash-command Skills,
MCP, agent, model, file, and context loop.

The next frontend milestone is therefore not another generic reskin. It is
experience parity for the daily user loop:

```text
open workspace
  -> choose or create a conversation
  -> type "/" or click composer controls
  -> select Skills / MCP tools / agents / models / files / context
  -> submit a governed run
  -> watch events, permissions, artifacts, and results in the same workspace
```

## 2. Goals

| Goal | Target outcome |
| --- | --- |
| Reference chat shell absorption | Absorb the user-approved LibreChat / poco-claw style chat shell: conversation rail, central thread, bottom composer, right-side artifacts/context panel, and dense enterprise navigation. |
| Product shell parity | The authenticated workspace visually and behaviorally reads as ai-platform's enterprise AI workbench, not as LambChat with renamed labels. |
| Slash-command composer | The composer supports `/skill`, `/mcp`, `/agent`, `/model`, `/file`, and `/context` command flows with keyboard-first search and selection. |
| Skills shortcut trigger | Typing `$` opens or filters directly to the Skills selector, so company users can invoke Skills without learning backend route names. |
| Skill-first workflow | Users can discover, select, inspect, and invoke allowed Skills directly from the composer, with selected Skills rendered as durable chips/tokens before send. |
| File reference chips | Uploaded, selected, or referenced files render as durable chips with name, type/source, permission/upload state, preview/remove affordance, and safe run-payload binding. |
| MCP tool workflow | Users can discover and select allowed MCP tools from the composer and MCP pages without gaining unmanaged server lifecycle or write-tool privileges. |
| Department-aware market | Marketplace and Skills pages show availability by tenant, department, group, role, and user where backend projections exist; missing contracts render explicit unavailable states. |
| Group toggle governance UI | Admin marketplace and Skill detail surfaces expose department/group availability toggles only when backed by ai-platform policy APIs; otherwise they show read-only/unavailable states. |
| Session sharing and channel import | Users can share sessions and import channel conversations only through governed ai-platform ACL/channel projections. |
| Company launchpad entry | `/apps` remains the company application launchpad and home entry for internal systems, while chat remains the primary AI workbench. |
| Admin continuity | Admin users can manage backed governance surfaces without leaving the product shell: runtime, Skills governance, tool policies, MCP policy, departments, users, roles, and audit where available. |
| Backend authority | The frontend never treats imported project state, local browser state, or copied backend contracts as product truth. ai-platform backend projections remain authoritative. |
| Screenshot acceptance | UI acceptance requires screenshots or browser evidence for core routes and workflows, not only lint, build, projection audit, or HTTP smoke. |

## 3. Non-Goals

| Non-goal | Reason |
| --- | --- |
| Replacing ai-platform backend contracts | The product is ai-platform's governed control plane, not the reference frontend's backend. |
| Treating `@` mentions as Skills parity | `@` may remain for persona, role, user, or context mention. It does not satisfy slash-command Skill/MCP selection. |
| Treating `$` as a backend shortcut | `$` is only a Skills-focused UI trigger. It must not bypass RBAC, skill release state, snapshot pinning, or permission policy. |
| Treating file chips as raw path exposure | File reference chips must bind to safe file/artifact IDs or upload handles, not raw local paths, executor paths, storage keys, or private runtime payloads. |
| Hiding missing product contracts behind fake UI | If department marketplace, MCP policy assignment, users/roles, or model administration lacks backend authority, the UI must show a backed read-only state or an explicit Phase 2 unavailable state. |
| Broadening ordinary-user tool authority | Ordinary users may select allowed tools and request governed execution; they must not gain unmanaged MCP server lifecycle, raw tool writes, or policy bypasses. |
| Migrating company app business modules into chat | `/apps` is an entry launchpad. It must not absorb nonGMPlims Vue business pages, target-system permissions, todos, dashboards, workflows, or statistics into ai-platform. |
| Closing experience acceptance through static smoke only | HTTP 200, build provenance, and API health prove deployability, not product usability. |

## 4. Reference And Evidence Boundary

The implementation issue must pin the preferred reference frontend source,
screenshots, or product walkthrough before coding. The pinned reference must
identify:

- target workspace layout,
- target composer behavior,
- target command-menu behavior,
- target Skills/MCP/agent/model selection behavior,
- target `$` Skills shortcut behavior if the reference supports it, or the
  deliberate ai-platform-owned behavior if it does not;
- target file reference chip behavior;
- target session share and channel import surfaces;
- target company application launchpad relationship to the chat shell;
- target admin/governance navigation behavior,
- exact source paths copied or adapted, if any,
- license/provenance note for copied code.

If the reference is unavailable or cannot be legally copied, the implementation
must recreate the behavior and visual structure inside `frontend/web` using
ai-platform-owned components. It must not import backend contracts, persistence
models, auth rules, or data-provider calls from the reference project.

## 5. Target User Experience

### 5.1 Workspace Shell

The first authenticated screen is the workbench, not a marketing page or a
generic admin menu.

Required areas:

- left conversation/workspace rail with new chat, search, recent conversations,
  pinned workflows, and visible tenant/workspace scope;
- central thread with messages, streaming events, tool calls, permission
  requests, artifacts, and readable long-form output;
- bottom composer with command entry, attachments, model/agent/status controls,
  and clear submit/stop states;
- right panel or drawer for artifacts, run playback, selected context,
  permission history, and provenance;
- admin/governance entry points that are present but permission-gated.

The shell should absorb the user-approved LibreChat / poco-claw behavior and
visual density: a fast conversation rail, compact message surface, grounded
composer, right-side context/artifact drawer, and immediate access to work
surfaces without marketing-style chrome. It must use a cohesive ai-platform
visual system. It must not expose LambChat brand authority, LambChat visual
identity, or a mixed old-shell/new-shell composition as the primary user
experience.

The company application launchpad is part of this shell contract. `/apps`
should remain reachable as the company home/application directory for existing
internal systems, while `/chat` remains the AI workbench route. The two routes
must share the same authenticated shell language and navigation hierarchy, but
`/apps` must keep the boundary that it links to existing systems instead of
owning their business workflows.

### 5.2 Composer And Slash Commands

The composer is the primary product surface.

Required behavior:

- typing `/` opens a command menu anchored to the composer;
- typing `$` opens a Skills-first selector or filters the command menu directly
  to allowed Skills;
- command groups include Skills, MCP tools, agents, models, files, context, and
  help/status commands;
- keyboard navigation supports up/down, enter, escape, search filtering, and
  restoring focus to the textarea;
- selected entities render as chips/tokens with icon, label, type, and remove
  affordance;
- selected Skills and MCP tools are included in the run request through existing
  ai-platform projections or fail closed if no safe backend contract exists;
- disabled, unavailable, permission-required, and admin-only options appear with
  clear state and no hidden execution path;
- `@` mention remains separate from `/`: it can select persona, role, user, or
  context references but cannot be the only Skills selection workflow.

The minimum Phase 1B command set is:

| Command | User-visible outcome | Backend authority |
| --- | --- | --- |
| `/skill` | Search allowed Skills, inspect description/risk, add selected Skill chip. | Existing governed skill or agent-app projection where available. |
| `$` | Open or filter directly to allowed Skills, add selected Skill chip. | Same authority as `/skill`; no bypass of release/RBAC/policy checks. |
| `/mcp` | Search allowed MCP tools, add tool chip or see policy-blocked state. | Existing tool-policy/admin projection where available; no unmanaged server writes. |
| `/agent` | Select platform-approved agent/app. | `/api/ai/agent-apps` or current safe projection. |
| `/model` | Select allowed model or show restricted state. | Existing public model projection where available. |
| `/file` | Attach allowed file types and show upload state. | Existing upload/artifact ACL contracts. |
| `/context` | Attach selected conversation/context/memory scope where backed. | Existing memory/context projection or Phase 2 unavailable state. |

### 5.2.1 File Reference Chips

File selection must be visible before send and understandable to non-technical
company users.

Required behavior:

- uploaded files, referenced run artifacts, and selected conversation files
  render as chips near the composer;
- each chip shows a file icon/type, display name, source, upload or ACL state,
  and remove affordance;
- chips with failed upload, blocked file type, expired ACL, or permission denial
  show a clear state and cannot silently enter the run payload;
- clicking a chip opens a safe preview or details drawer when backed by
  ai-platform artifact/file APIs;
- run submission binds chips to safe upload handles, artifact IDs, or file
  reference IDs, never raw paths or executor-private storage identifiers.

### 5.3 Skills Hub And Marketplace

Skills must feel native to the chat loop and also remain governable.

Required ordinary-user surfaces:

- browse available Skills by category, department, popularity/recent use, and
  permission state where backend projections exist;
- browse marketplace entries using compact filters and group/dept availability
  indicators, not only a flat list;
- inspect Skill description, version/source, required inputs, expected outputs,
  allowed tools, risk level, and approval requirements;
- add a Skill to the current composer or start a run from the Skill detail page;
- see "not available for your department" or "requires admin enablement" states
  when access is denied.

Required admin/governance surfaces:

- released/staged Skill inventory;
- version/snapshot pinning and rollback;
- review evidence, SBOM/license/vulnerability fields when available;
- department/group/role/user availability controls only after backend contracts
  exist;
- group toggle UI for Skill availability, with explicit enabled, disabled,
  inherited, and unavailable states;
- audit trail for enable/disable/install/publish actions.

Phase 1B may show backed read-only availability and fail-closed placeholders.
Phase 1C may integrate already-backed marketplace and governance pages into the
new shell. Phase 2 is required for true department/group install/enable/disable
policy if backend routes do not yet exist.

### 5.4 MCP Tools

MCP must be visible as a governed tool capability, not as raw server management
for ordinary users.

Ordinary users can:

- search visible tools from `/mcp`;
- inspect tool name, server/source, description, risk, and permission mode;
- add allowed tools to the composer as chips;
- see deny/request/admin-only states when blocked.

Admins can, where backend contracts exist:

- view server and tool inventory;
- assign policies by department/role/user;
- review allow/ask/deny history;
- see redacted audit evidence.

MCP server lifecycle, credential management, and policy assignment are Phase 2
unless current ai-platform backend projections already provide safe authority.

### 5.5 Session Sharing And Channel Import

Session sharing and channel import are collaboration surfaces, not public link
shortcuts.

Required session share behavior:

- users can create share links or share targets only when ai-platform ACL rules
  allow it;
- the share page shows the conversation title, allowed messages/artifacts,
  redaction state, owner/workspace, expiration or revocation state, and a clear
  permission-denied page for unauthorized viewers;
- shared artifacts and files keep their original ACL and preview allowlist
  behavior;
- revoked or expired shares fail closed and do not reveal session metadata.

Required channel import behavior:

- users can browse available channel sources where backend projections exist,
  such as governed Feishu/Lark/team-channel imports;
- imported messages appear as selectable context/file reference chips before
  they enter a run;
- import UI shows source channel, author/time range, redaction/retention state,
  and permission warnings;
- missing channel APIs render a Phase 2 unavailable state instead of fake import
  success.

### 5.6 Company Application Launchpad

The company launchpad is a first-class authenticated route in the frontend
program.

Required behavior:

- `/apps` shows the internal application directory and existing web/app links;
- the launchpad uses the same shell language as the chat workbench;
- launchpad cards open existing systems in a new tab and do not assume ownership
  of target-system auth, permissions, or workflow state;
- future backend-managed catalogs may add department-scoped visibility, but
  Phase 1 static entries must not pretend to enforce target-system permissions.

### 5.7 Visual Quality

The product must read as an enterprise AI workbench:

- dense but scannable layout;
- restrained palette with semantic status colors;
- no decorative hero treatment inside the authenticated app;
- no emoji as structural icons;
- stable spacing and component dimensions;
- accessible focus states and touch/click targets;
- no nested card clutter;
- no overlapping text or controls at desktop, laptop, and narrow widths;
- clear loading, disabled, empty, denied, and error states.

## 6. Architecture Boundary

Implementation remains inside `frontend/web`.

```text
Browser workspace
  -> ai-platform shell and composer components
  -> ai-platform service adapters
  -> ai-platform public/admin projections
  -> ai-platform backend control plane
  -> queue / worker / executor / sandbox / events / artifacts / audit
```

Required boundaries:

- Auth and session use ai-platform principal/session contracts.
- Route and action visibility derive from ai-platform roles, permissions,
  tenant, workspace, and admin state.
- Skills use ai-platform skill snapshots, release state, and permission policy.
- MCP uses ai-platform tool policy and redacted projections.
- Artifacts and files use ai-platform ACL and preview/download routes.
- The frontend cannot read raw runtime paths, raw storage keys, raw queue
  payloads, provider secrets, executor-private payloads, or sandbox workdirs.

## 7. Delivery Phases

| Phase | Name | Outcome | Required evidence |
| --- | --- | --- | --- |
| 1A | Foundation, auth, RBAC, API shell | Existing frontend foundation runs on official entry and uses ai-platform auth/RBAC/projections. | Build, projection audit, packaged/static smoke, API health, auth/RBAC smoke. |
| 1B | Chat shell and composer parity | LibreChat / poco-claw style workspace shell, `/` command menu, `$` Skills shortcut, file reference chips, and selected Skill/MCP/agent/model chips meet this PRD while using current backend contracts or fail-closed states. | Component tests, projection audit, browser screenshots, composer interaction smoke, 211 smoke. |
| 1C | Governance and collaboration surface parity | Skills hub, marketplace, group toggle UI, MCP page, session share page, channel import UI, company launchpad navigation, admin runtime, and backed governance pages are integrated into the shell with permission-gated UX. | Admin/ordinary browser smoke, deny-path tests, projection audit, screenshots. |
| 2 | Backend-backed expansion | Department/group Skill marketplace policy, MCP policy assignment, session share ACLs, channel import projections, users/roles/departments, model admin, settings, and notification workflows become real backed product surfaces where Phase 1 only had placeholders. | Backend schema/route tests, RBAC deny tests, frontend happy/deny tests, 211 runtime smoke. |

## 8. Acceptance Criteria

### 8.1 Product Acceptance

- The authenticated app no longer visually reads as LambChat or as a mixed shell.
- The authenticated chat shell follows the approved LibreChat / poco-claw
  reference pattern without importing its backend authority.
- Ordinary users can complete: open chat -> choose Skill from `/skill` -> choose
  agent/model as allowed -> attach file/context as allowed -> submit run -> see
  streaming progress -> respond to permission request -> inspect/download
  artifact.
- Ordinary users can type `$`, select an allowed Skill, see a durable Skill
  chip, and remove it before send.
- Ordinary users can attach or reference a file and see a durable file chip with
  preview/remove/error state before send.
- Ordinary users can discover Skills from both the composer and Skills hub.
- Ordinary users can discover MCP tools from the composer and MCP surface without
  unmanaged server or credential controls.
- Ordinary users can reach `/apps` as the company application launchpad without
  confusing it for migrated nonGMPlims business modules.
- Session share and channel import surfaces exist where backed, or show explicit
  Phase 2 unavailable states where backend contracts are missing.
- Admin users can reach backed governance surfaces from the same shell.
- Missing backend contracts are represented as explicit unavailable states, not
  fake working UI and not hidden backend shortcuts.

### 8.2 Composer Acceptance

- `/` opens a command menu from an empty composer and after whitespace.
- `$` opens or filters directly to a Skills selector from an empty composer and
  after whitespace.
- `/skill`, `/mcp`, `/agent`, `/model`, `/file`, and `/context` command groups
  are present.
- Search filters command results and preserves keyboard navigation.
- Selected Skills/MCP tools/agents/models/files/context render as removable
  chips/tokens.
- File chips show upload/ACL/error state and never expose raw runtime paths,
  storage keys, or executor-private paths.
- Sending includes selected backed entities in the safe run payload or blocks
  with a clear unavailable/permission message.
- `@` mention remains functional but is not the Skills/MCP command path.

### 8.3 Governance Acceptance

- Ordinary-user UI cannot access admin-only Skill publish/install, MCP server
  lifecycle, user/role/dept CRUD, model admin writes, or tool policy writes.
- Marketplace group toggles cannot mutate availability unless backed by
  ai-platform department/group/role policy APIs and permission checks.
- Session share cannot reveal revoked, expired, cross-tenant, or unauthorized
  sessions, files, artifacts, or metadata.
- Channel import cannot read or import channels outside the user's authorized
  workspace/source projection.
- Denied options show a clear denied, request, or admin-only state.
- All high-risk tool actions still flow through ai-platform permission policy.
- Projection audit reports no active private-payload violations for the browser
  graph.

### 8.4 Visual Acceptance

Every implementation PR that changes shell, composer, Skills, Marketplace, MCP,
or admin navigation must include screenshot or browser evidence for:

- authenticated workspace overview;
- composer closed state;
- `/` command menu;
- `$` Skills shortcut menu;
- selected Skill chip;
- selected MCP tool chip or policy-denied state;
- file reference chip with normal and denied/error state;
- Skills hub;
- Marketplace with group toggle UI or marketplace unavailable state;
- MCP page or MCP unavailable state;
- session share page or share unavailable/denied state;
- channel import UI or channel import unavailable/denied state;
- company launchpad `/apps`;
- ordinary-user denied admin route;
- admin-visible governance route.

Screenshots must use the official or preview 211 entry when claiming 211
verification. Local screenshots can support `local partial` or `PR ready`, but
do not prove `211 verified`.

### 8.5 Technical Acceptance

- `frontend/web` remains the only production frontend source.
- New components use ai-platform service adapters, not imported backend data
  providers from the reference project.
- Frontend tests cover happy and deny paths for composer command selection.
- Projection audit, typecheck, lint, build, and affected tests pass before PR
  readiness.
- Runtime-affecting changes receive 211 smoke evidence before `211 verified`.

## 9. Definition Of Done

Phase 1B is done only when:

1. The official or preview frontend shows the new ai-platform workspace shell.
2. The composer satisfies slash-command and `$` Skills shortcut acceptance.
3. A governed Skill can be selected from the composer and carried into a run or
   blocked with a precise backend-contract reason.
4. File reference chips bind to safe upload/file/artifact references and show
   preview/remove/deny state.
5. MCP tools are visible through governed selection or explicit unavailable
   state.
6. Screenshots prove the primary workflow and denied states.
7. Projection audit and focused frontend tests pass.
8. 211 smoke proves the deployed source and official/preview entry match the
   accepted build when claiming `211 verified`.

Phase 1C is done only when Skills marketplace/group toggles, MCP page, session
share, channel import, `/apps` launchpad navigation, and backed admin/governance
routes are either functional through ai-platform APIs or rendered as explicit
fail-closed Phase 2 unavailable states.

The frontend experience is `gate closable` only after Phase 1B, Phase 1C, and
the required company-account browser login, ordinary-user workflow, and admin
workflow smoke pass on the official 211 entry.

## 10. Open Risks

| Risk | Mitigation |
| --- | --- |
| Reference frontend is underspecified | Pin source commit and screenshots before implementation. |
| Static smoke is mistaken for UX acceptance | Require screenshot/browser evidence for visual and composer flows. |
| Slash commands or `$` shortcut exceed current backend contracts | Use backed chips where possible and fail closed with explicit Phase 2 issue links where not possible. |
| File chips leak raw paths or stale access | Bind chips to safe file/artifact handles and cover denied/expired states in tests. |
| MCP becomes unmanaged tool execution | Keep ordinary-user MCP to visible, governed selection; reserve lifecycle and policy writes for admin-backed contracts. |
| Department/group marketplace requires backend work | Split read-only/unavailable Phase 1C from true Phase 2 department/group policy management. |
| Share/import screens imply collaboration authority before APIs exist | Show fail-closed unavailable states until ai-platform ACL/channel projections exist. |
| Company launchpad is mistaken for old app migration | Keep `/apps` as click-through launchpad and document nonGMPlims boundary in implementation PRs. |
| Visual reskin remains superficial | Make workspace, composer, command menu, Skills hub, and MCP route screenshots required evidence. |

## 11. Immediate Next Actions

1. Open a Phase 1B issue for `chat experience parity: workspace shell + slash composer`.
2. Pin the preferred reference frontend source and screenshot set in that issue.
3. Convert PR #111's outcome wording to `Phase 1A foundation`, not final frontend migration.
4. Add composer command tests before implementation: `/`, `/skill`, `/mcp`,
   `$`, keyboard selection, Skill/MCP/file chip rendering,
   deny/unavailable state.
5. Implement shell/composer parity before expanding department marketplace or MCP
   server lifecycle features.
6. Open follow-up issues for marketplace group toggle UI, session share page,
   channel import UI, and launchpad-shell integration so they are tracked as
   Phase 1C surfaces rather than loose future polish.
