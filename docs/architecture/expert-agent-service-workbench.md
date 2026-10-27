# Expert Agent Service Workbench

Status: implementation contract
Design ID: `ai-platform.expert-agent-service-workbench.v1`
Source baseline: `5176578bed089b7b479cefc528b240c2edf94c31`

## Purpose

The authenticated product offers general Chat and published expert services in
one shared conversation frame. General Chat supports open-ended questions;
an expert workspace makes its public identity and task examples clear while
keeping its pinned conversation and governed capabilities server-owned. Users
do not need to understand Skill, MCP, revision, or prompt plumbing.

This document is the implementation checklist for the Agent Market, Agent
Builder, Skill administration, and authenticated navigation changes in this
slice. It is a source contract only. It does not establish deployed runtime or
ordinary-user acceptance.

## Product Language

- **Expert Agent** is the user-facing service selected from the Agent Market.
- **Agent.md initial instructions** is the Builder label for the existing
  private `instructions` field. It is system-level initialization owned by the
  published Agent Profile; it is not a user message and is never returned in a
  public projection.
- **Agent Skill Set** is the set of professional capabilities authorized by the
  Agent Profile and made available to the Agent SDK. The profile stores stable
  Skill names; publication or Run admission resolves each name against the
  tenant-authorized catalog, and the accepted Run freezes the resolved versions.
  It is not ordinary chat, and membership does not require invocation on every
  task.
- **Task** is the user-facing name for work created inside an Agent Workspace.
  The UI should use task-oriented labels instead of generic Chat labels.
- **Archive Skill** means disabling and removing the tenant distribution from
  the active catalog while retaining immutable versions, historical Runs, and
  audit evidence. It does not mean deleting the global Skill record.

The product label `Agent.md` does not introduce a new file-upload format or a
second persistence model. The canonical field remains `instructions`, restored
server-side and appended to the executor system prompt.

## Scope

### Authenticated navigation

1. The authenticated root and post-login fallback open Company Navigation at
   `/apps`. Bare `/chat` opens general Chat, reachable from the primary
   navigation `对话` entry and from the collapsed/mobile rail.
2. General Chat exposes new-conversation and search controls. The user's global
   session history remains a disclosure-safe list grouped by public Agent
   identity. At the bottom of the expanded sidebar, administrators have a
   collapsible `管理` group with a shield icon; ordinary users instead have
   `可用能力` with a layers icon and the existing Skill/MCP destinations.
   These are presentation groups, not new permission grants. The separate
   account-footer gear retains the existing settings menu and its actions;
   platform resource navigation is never moved into that menu.
3. `/chat/:sessionId` keeps the canonical Chat reader and recovers its original
   Agent binding. Agent Workspace reuses the same streaming, files, history, and
   composer implementation.
4. Agent Workspace and general Chat share one 272px primary sidebar that owns
   global navigation and history. Agent Workspace keeps two distinct history
   scopes: global session history and Agent-scoped task history, presented
   through one in-sidebar scope switch (`任务历史` / `全部对话`) instead of an
   adjacent second history sidebar. Both surfaces stay mounted so a successful
   rename/delete receipt reaches the hidden scope, and only one is presented at
   a time; paginated and global reads remain separate server projections scoped
   to the current authenticated principal. A global history selection opens the
   session under its original Agent or generic compatibility route and never
   reclassifies it as a task for the currently selected Agent. Workspace
   controls still use task language: “start new task” and “task history”; they
   must not expose generic Chat creation or search.
   Selecting an Agent task commits its route immediately. The existing route
   identity and history lifecycle owns recovery and supersession, including a
   return to the previously open task while another identity read is pending.

The compatibility `sessionApi.get` read converts a failure to `null` only when the
shared safe-error owner produces `ApiRequestError` with status 404. Public/localized
error text is never an HTTP discriminator. Authentication, transient failures,
protocol errors and cancellation retain their original error object; forced
re-login classification remains owned by `authFetch`. The authoritative identity
read continues to reject missing sessions. Existing title, history and notification
consumers retain the nullable compatibility contract. The owning regression is
`services/api/__tests__/session.test.ts`, selected once by the existing `test:sse`
CI lane alongside the fetch parser and history consumers.

### Agent Market and Workspace

1. The Market leads with the outcome: choose an expert and start a task.
2. Card and detail primary actions use “Start task”. They must continue to build
   the existing revision-bound Agent Workspace URL.
3. Cards summarize up to three recommended tasks as “Suitable for”, without
   exposing private instructions or server capability identifiers.
4. The detail view tells a first-time user that model selection is platform-owned
   and the expert already owns its governed Skill configuration.
5. Empty and no-match states provide a direct recovery action. Network and
   authority errors remain fail-closed.
6. Agent Workspace starters are presented as ready-to-run task examples and
   keep the existing explicit-send behavior.

### Public model catalog and selection

The shared composer uses `ChatInputToolbar` and the Elements `ComposerModelPicker` to select
an administrator-enabled public model. `ModelCatalogProvider` reads business
groups from `/api/ai/models/groups` before `/api/agent/models/available?group_id=...`.
Its unified `refreshModels` refreshes both projections on picker open (including
slash commands), visible focus/foreground return, successful admin publication,
and same-page group changes. Concurrent reads merge; publication supersedes old
reads. Subject/group switches and unmount abort and fence stale completions.
Pending/transient refreshes retain same-subject options and valid selections;
401/403 clears grants. Empty catalogs retain the picker retry entry. Lost groups
or explicit models require reselection, never an automatic alternative; frozen
Runs are unchanged. `default_model_id`
is the public catalog's administrator-selected default; the composer resolves
its model value, falling back to the first available model when necessary.

The unmounted `components/agent/ModelSelector` and its private click-away guard
are retired. Catalog hydration has no per-user pin read, toggle, or automatic
metadata cleanup. The generic Identity profile/metadata API, persistence, and
historical `pinned_model_ids` values remain compatible and are not migrated or
deleted. Shared model icons remain in use by messages and the catalog panel.

`test:model-admin` keeps mounted catalog subject-isolation, denial/logout,
and cancellation coverage. It also checks the real public API transport makes
only the catalog GET, preserves historical metadata, and resolves the public
default and fallback. Browser-route and Agent Market fixtures no longer stub
the retired pin client; generic backend metadata compatibility tests remain.

### Agent Builder

The initial editing surface contains only these core fields:

| Field | Product label | Reason |
| --- | --- | --- |
| `name` | Expert name | Public identity |
| `instructions` | Agent.md initial instructions | Private system initialization |
| `skill_set` | Skill Set | One or more authorized Skill-name references; publication or Run admission resolves and freezes versions |

All other fields remain supported but are progressive configuration:

- **Market presentation:** description, avatar, category, capability summary,
  welcome message, recommended tasks, starter prompts, expected outputs.
- **Inputs and tools:** optional attachment guidance and MCP tool selection.
  Attachment formats are governed by the platform upload and content-safety
  boundary, not configured as an Agent or Skill dispatch requirement.
- **Access governance:** visibility, departments, roles, users, permission and
  data-access notice.

The first save must not be blocked by presentation-only fields. The frontend
may derive safe display fallbacks for an incomplete draft, but it must not
manufacture private instructions, model, Skill, ACL, or capability authority.
Publishing may continue to surface quality guidance without moving authority to
the browser.

### Expert administration

1. A published expert must be withdrawn before deletion is enabled.
2. Delete retires the active profile identity and removes it from the admin
   directory; it does not erase immutable revisions, Sessions, Runs, messages,
   or audit evidence, and the retired Agent ID cannot be reused.
3. Ordinary global and expert-scoped history navigation lists only conversations
   whose profile identity remains active and whose current lifecycle is
   published. An owner may retain read-only access through an existing exact
   Session link for audit continuity.
4. The confirmation states the retained evidence and irreversible ID retirement.

### Skill administration

1. Destructive copy and icons use “Archive”, not “Delete”.
2. The confirmation explains that active use stops while immutable versions,
   historical Runs, and audit evidence remain.
3. A successful archive immediately removes the row from the active catalog.
   A failed archive restores the row and leaves the confirmation available.
4. Batch archive removes only the server-confirmed successes and keeps failed
   selections available for retry.
5. The page presents catalog totals, runtime-enabled count, user-visible count,
   and the archive policy before the table.

## Authorities That Must Not Change

- Agent Conversations are created only after explicit user action and remain
  pinned to `agent_id`, immutable Revision, and `content_hash`.
- Every run, retry, resume, and copy reauthorizes ownership, tenant,
  publication, ACL, the Run-pinned model, the current authorized Skill versions,
  and MCP capability server-side. Agent Profile configuration stores Skill names;
  accepted Run snapshots carry the immutable resolved versions.
- Browser requests may select only an administrator-enabled public model. They
  cannot override private instructions, Skill, MCP, ACL, revision hash, or
  execution identity; the backend resolves and freezes the selected model and
  active connection revision when admitting the Run.
- Agent Profile `instructions` stay in private execution input and the executor
  system prompt. They never become user content or a safe public field.
- Expert Agents continue to require at least one authorized Skill name in their
  profile Skill Set. Publication or Run admission resolves each name against the
  current tenant-authorized catalog and freezes the resulting versions in the
  immutable Run snapshot. The Agent SDK autonomously decides whether and which
  registered Skill to invoke for each task. Ordinary Harness chat remains
  `execution_kind=harness_chat` with `skill_id=null`; historical `general-chat`
  is compatibility data, not a new product Skill.
- Admin Skill release remains the only global immutable version/review/promote/
  rollback authority. Public Skill and Marketplace routes remain projections
  and tenant-distribution controls.
- Skill archive mutates only the tenant capability distribution. It must not
  hard-delete global Skill versions, snapshots, Run references, or audit facts.

## Implementation Map

| Work package | Primary source | Required outcome |
| --- | --- | --- |
| Shared entry routing | `frontend/web/src/App.tsx`, auth and shell navigation | Root/login resolve to Company Navigation; `/chat` opens general Chat and existing session links preserve identity |
| Market task language | `frontend/web/src/features/agent-market/AgentMarketRoute.tsx` | Task-oriented hierarchy, CTA, examples, and recovery states |
| Workspace task navigation | `ChatAppContent.tsx`, `SessionSidebar.tsx`, sidebar parts | Agent-scoped “new task” and task history inside the single primary sidebar |
| Builder progressive disclosure | `AgentBuilderWorkbench.tsx`, `AgentBuilderEnterpriseFields.tsx`, `agentBuilderAdapter.ts` | Three-field initial surface; presentation and governance grouped as optional |
| Skill archive UX | `SkillsPanel/*`, `useSkills.ts`, locale files | Truthful archive semantics, optimistic removal/rollback, partial-result handling |

### Change Contract: run-scoped Thinking preference

- **Owner:** Execution model-option policy; Agent Profile admission only verifies
  that the option cannot alter the admitted capability set.
- **Bounded paths:** Chat request validation and admission, Run input projection,
  sandbox task configuration, Claude Agent SDK options, prompt language policy,
  Agent workspace composition, and structurally identified legacy Thinking
  presentation.
- **Reached invariants:** profile Skill/MCP/model authority remains unchanged;
  the existing platform-governed shared model selector remains available;
  caller-supplied server control metadata remains stripped; `auto` enables SDK
  adaptive thinking without an explicit effort, while `low`, `medium`, and
  `high` send the selected effort. Every level uses `display=omitted`, no SDK
  Thinking block is published, and private signatures and provider-internal
  reasoning remain outside public events.
- **Acceptance:** `auto`, `low`, `medium`, and `high` survive admission and
  reach the SDK exactly once; legacy `off` normalizes to `auto`; `max` and other
  unsupported values fail before persistence. Agent workspaces expose the
  selected level through the visible composer toolbar while keeping Skill and
  MCP controls locked and preserving the established platform-governed model
  selector; Chinese requests keep the final answer in Simplified Chinese.
- **Regression proof:** focused tests cover profile admission, trust-boundary
  validation, sandbox transport, all SDK effort levels, Agent workspace option
  composition, and public display fallbacks. Local evidence cannot prove gateway,
  model, or deployed behavior.
- **Rollback:** revert the source change as one unit; no schema or stored-data
  migration is introduced.
- **Stop conditions:** stop if the change admits a Skill, MCP, prompt, or model
  selector not already authorized, exposes raw provider reasoning, introduces
  model capability probing, or requires an SSE wire-contract change.

## Acceptance Checklist

### Source and component checks

- Authenticated `/` and the post-login fallback resolve to `/apps`; `/chat`
  renders the general entry and `/chat/:sessionId` stays direct-addressable.
  Generic Chat creation and search are reachable from the `对话` entry, while an
  Agent workspace still shows only “start new task” and “task history”.
- Agent Market card/detail navigation still carries exact `agent_id` and
  revision through the existing route builders.
- Opening a Workspace does not create a conversation; first explicit send uses
  `agentProfileApi.createConversation` with `selected_agent_profile` and one
  operation identity.
- Builder can save a draft with the three core fields and safe defaults for
  presentation fields without choosing a model. Missing any core field still
  blocks save.
- Public Agent payloads contain no `instructions`, raw Skill identity, MCP IDs,
  model ID, ACL details, or content hash.
- Single and batch Skill archive behavior matches the server-confirmed result.
- Every translated Skill archive key exists in all shipped locale bundles.
  Agent Market, Builder, and Workspace remain fixed-Chinese product surfaces;
  their task-oriented copy is covered by source and component tests.

### Browser checks

- Desktop and narrow viewport: Company Navigation stays the default
  authenticated surface; general Chat and Expert Market are both discoverable.
  Cards and recovery actions remain usable.
- Builder initially shows the three core fields; every progressive section can
  be opened with keyboard and retains entered values.
- Agent Workspace shows expert identity in the thread header, task examples, one
  in-sidebar history scope switch for task history and global session history,
  the shared enabled-model selector, and a task-oriented composer without
  exposing the other capability selectors. General Chat shows the `通用助手`
  header, the `今天想解决什么问题？` headline with its three suggestions that fill
  the draft without sending, and the same composer. No desktop view shows a
  second history sidebar.
- Global history selection preserves the session's original Agent identity (or
  generic `/chat/:sessionId` compatibility route) and does not bind another
  Agent's session into the currently open workspace.
- Skill Admin archive confirmation, loading state, success removal, failure
  recovery, and batch partial failure are visually and semantically clear.

### Evidence boundary

Typecheck, lint, component tests, browser mocks, and production build prove only
the candidate source contract. Deployment, real database migration, ordinary
principal ACL behavior, worker dispatch, Skill invocation, SSE, object storage,
and artifact download remain bound to
`docs/acceptance/agent-app/ordinary-user-matrix.md` and the release procedure.

## Rollout and Rollback

This slice stores authorized Agent Skill Set names in profile revisions and
resolves current governed versions at publication or Run admission before the
Agent SDK dispatches autonomously. The API, persistence, and execution
contracts remain compatible with historical version-bearing profile rows,
alongside the UI changes.
Rollback requires application and schema compatibility with legacy singleton
Skill revisions; it must not delete Agent revisions, conversations, Skill
distributions, Runs, or historical compatibility rows. Do not use rollback to
reintroduce mandatory Skill invocation, per-Agent file-type gates,
`general-chat` as a published Skill, or weaker server-owned Agent admission.

## Retired presentation surfaces

This change replaces presentation only. Run admission, conversation pinning,
Session isolation, authorization, and persistence are unchanged.

| Superseded surface | Disposition |
| --- | --- |
| `AgentConversationPanel.tsx` (adjacent desktop Agent history sidebar) | Deleted. Its panel geometry, collapse control, and duplicate new-conversation button are no longer rendered; the primary sidebar scope switch owns that presentation. Owning regression: `features/agent-market/__tests__/AgentConversationPanel.test.ts` asserts the zero-importer inventory and that the file is absent. |
| `AgentConversationPanel` props/`contentSidebar`/`agentHistoryInMainPanel` threading | Removed from `AppShell`, `ChatAppContent`, and `SessionSidebar`. No compatibility consumer exists outside the retired panel. |
| Bare `/chat` redirect and hidden generic Chat navigation | Replaced with the general entry and shared navigation; `/chat/:sessionId` identity recovery is unchanged. Owning regressions: `AppRouteFallback.test.ts`, `launchpadRoute.test.ts`, and sidebar navigation tests. |
| One `管理` label and gear icon for every user's platform navigation group | Replaced by admin `管理` (shield) and ordinary `可用能力` (layers, Skill/MCP only). The footer settings gear and existing authorization policy are unchanged. `GeneralChatEntryReset.mounted.test.tsx` verifies each role's rendered destinations and independent gear action. |
| Always-on empty-state mascot badge and time-of-day greeting | Removed from `WelcomePage`; the branded `/icons/icon.svg` asset is unchanged and still used elsewhere. |
| `68rem` / `60rem` message/composer column and `288px` sidebar width | Replaced by an aligned, responsive `76rem` maximum column for messages, composer, status and skeletons; the shared sidebar remains `272px` in `librechat-ui/surface.ts`. Only visual geometry changes; chat controls and behavior are unchanged. |

Verification of the retirement is the source inventory in
`AgentConversationPanel.test.ts` plus the mounted scope-switch coverage in
`AgentWorkspaceHistory.mounted.test.tsx`; no deleted path is left active without
an importing caller.

## Rejected Alternatives

- Creating a second `Agent.md` file store: it would split authority from the
  canonical `instructions` revision field.
- Sending Agent instructions as the first user message: it would leak private
  execution definition and change conversation semantics.
- Removing canonical Chat components: Agent Workspace depends on their durable
  history, file, SSE, and composer behavior.
- Hard-deleting Skills: immutable release, Run, snapshot, and audit references
  require retained evidence.
- Letting the Builder browser infer Skill, MCP, or ACL defaults: those are
  server-authorized definition fields, not presentation defaults. Models are
  intentionally selected from the shared governed catalog at Run admission and
  are not Agent Profile definition fields.

## Independent SOP application request ownership

The authenticated `/ai-apps/sop-assistant` page remains an independent RAGFlow
application, not a platform Run or MCP invocation. Its `sopTransport` feature
module owns initialization/answer stream readers, the shared request deadline,
and local transport cleanup. The page owns cancellation and a request generation
bound to the current authenticated tenant/user and non-secret browser session
marker. Async completions recheck the current marker as well as the generation;
the same user signing in again cannot inherit an earlier request/session. Leaving the page or changing
that scope aborts its request and clears local conversation state; late progress,
results, errors and completion cannot update the replacement scope.

Reader cancellation only closes the local request. It is not evidence of remote
session deletion or remote computation cancellation. Clearing the conversation
continues to clear local messages/session selection only. Existing endpoint,
share-token configuration, request fields and answer accumulation stay unchanged;
this resource-lifecycle boundary does not add an upstream authorization system.
