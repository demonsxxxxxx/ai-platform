# Expert Agent Service Workbench

Status: implementation contract
Design ID: `ai-platform.expert-agent-service-workbench.v1`
Source baseline: `5176578bed089b7b479cefc528b240c2edf94c31`

## Purpose

The authenticated product is an expert-agent service, not a generic chat
product with an Agent add-on. An ordinary user should select a published expert,
describe a task, and work inside that expert's pinned conversation without
having to understand model, Skill, MCP, revision, or prompt plumbing.

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
- **Primary Skill** is the exact required professional capability pinned by the
  Agent Profile. It is not ordinary chat and it is not an optional prompt hint.
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

1. The authenticated root, post-login fallback, and bare `/chat` route open the
   Agent Market.
2. Generic Chat creation, search, and history controls are not discoverable in
   authenticated navigation.
3. Existing `/chat/:sessionId` deep links remain a compatibility reader in this
   slice. The canonical Chat components remain because Agent Workspace reuses
   their streaming, files, history, and composer implementation.
4. Agent Workspace navigation retains its Agent-scoped task history and uses
   task language: “start new task” and “task history”. It must not expose a
   generic Chat entry.

### Agent Market and Workspace

1. The Market leads with the outcome: choose an expert and start a task.
2. Card and detail primary actions use “Start task”. They must continue to build
   the existing revision-bound Agent Workspace URL.
3. Cards summarize up to three recommended tasks as “Suitable for”, without
   exposing private instructions or server capability identifiers.
4. The detail view tells a first-time user that model and Skill configuration
   is already owned by the expert.
5. Empty and no-match states provide a direct recovery action. Network and
   authority errors remain fail-closed.
6. Agent Workspace starters are presented as ready-to-run task examples and
   keep the existing explicit-send behavior.

### Agent Builder

The initial editing surface contains only these core fields:

| Field | Product label | Reason |
| --- | --- | --- |
| `name` | Expert name | Public identity |
| `instructions` | Agent.md initial instructions | Private system initialization |
| `model_id` | Runtime model | Exact server-authorized model selection |
| `selected_skill` | Primary Skill | Exact required Skill/version binding |

All other fields remain supported but are progressive configuration:

- **Market presentation:** description, avatar, category, capability summary,
  welcome message, recommended tasks, starter prompts, expected outputs.
- **Inputs and tools:** file input, supported file types, MCP tool selection.
- **Access governance:** visibility, departments, roles, users, permission and
  data-access notice.

The first save must not be blocked by presentation-only fields. The frontend
may derive safe display fallbacks for an incomplete draft, but it must not
manufacture private instructions, model, Skill, ACL, or capability authority.
Publishing may continue to surface quality guidance without moving authority to
the browser.

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
  publication, ACL, model, Skill version, and MCP capability server-side.
- Browser requests cannot override private instructions, model, Skill, MCP,
  ACL, revision hash, or execution identity.
- Agent Profile `instructions` stay in private execution input and the executor
  system prompt. They never become user content or a safe public field.
- Expert Agents continue to require their exact Primary Skill. Ordinary Harness
  chat remains `execution_kind=harness_chat` with `skill_id=null`; historical
  `general-chat` is compatibility data, not a new product Skill.
- Admin Skill release remains the only global immutable version/review/promote/
  rollback authority. Public Skill and Marketplace routes remain projections
  and tenant-distribution controls.
- Skill archive mutates only the tenant capability distribution. It must not
  hard-delete global Skill versions, snapshots, Run references, or audit facts.

## Implementation Map

| Work package | Primary source | Required outcome |
| --- | --- | --- |
| Agent-first routing | `frontend/web/src/App.tsx`, auth and shell navigation | Root/login/bare Chat resolve to Agent Market; generic controls hidden |
| Market task language | `frontend/web/src/features/agent-market/AgentMarketRoute.tsx` | Task-oriented hierarchy, CTA, examples, and recovery states |
| Workspace task navigation | `ChatAppContent.tsx`, `SessionSidebar.tsx`, sidebar parts | Agent-scoped “new task” and “task history”; no generic Chat discovery |
| Builder progressive disclosure | `AgentBuilderWorkbench.tsx`, `AgentBuilderEnterpriseFields.tsx`, `agentBuilderAdapter.ts` | Four-field initial surface; presentation and governance grouped as optional |
| Skill archive UX | `SkillsPanel/*`, `useSkills.ts`, locale files | Truthful archive semantics, optimistic removal/rollback, partial-result handling |

## Acceptance Checklist

### Source and component checks

- Authenticated `/` and `/chat` resolve to `/agent-market` while
  `/chat/:sessionId` remains direct-addressable.
- Agent Market card/detail navigation still carries exact `agent_id` and
  revision through the existing route builders.
- Opening a Workspace does not create a conversation; first explicit send uses
  `agentProfileApi.createConversation` with `selected_agent_profile` and one
  operation identity.
- Builder can save a draft with the four core fields and safe defaults for
  presentation fields. Missing any core field still blocks save.
- Public Agent payloads contain no `instructions`, raw Skill identity, MCP IDs,
  model ID, ACL details, or content hash.
- Single and batch Skill archive behavior matches the server-confirmed result.
- Every translated Skill archive key exists in all shipped locale bundles.
  Agent Market, Builder, and Workspace remain fixed-Chinese product surfaces;
  their task-oriented copy is covered by source and component tests.

### Browser checks

- Desktop and narrow viewport: Agent Market is the first authenticated surface,
  cards and recovery actions remain usable, and no generic Chat entry appears.
- Builder initially shows the four core fields; every progressive section can
  be opened with keyboard and retains entered values.
- Agent Workspace shows expert identity, task examples, task history, and a
  task-oriented composer without exposing capability selectors.
- Skill Admin archive confirmation, loading state, success removal, failure
  recovery, and batch partial failure are visually and semantically clear.

### Evidence boundary

Typecheck, lint, component tests, browser mocks, and production build prove only
the candidate source contract. Deployment, real database migration, ordinary
principal ACL behavior, worker dispatch, Skill invocation, SSE, object storage,
and artifact download remain bound to
`docs/acceptance/agent-app/ordinary-user-matrix.md` and the release procedure.

## Rollout and Rollback

This slice is additive UI behavior over existing API and persistence contracts.
Rollback may restore the former labels and disclosure layout without deleting
Agent revisions, conversations, Skill distributions, Runs, or historical
compatibility rows. Do not use rollback to reintroduce `general-chat` as a
published Skill or to weaken server-owned Agent admission.

## Rejected Alternatives

- Creating a second `Agent.md` file store: it would split authority from the
  canonical `instructions` revision field.
- Sending Agent instructions as the first user message: it would leak private
  execution definition and change conversation semantics.
- Removing canonical Chat components: Agent Workspace depends on their durable
  history, file, SSE, and composer behavior.
- Hard-deleting Skills: immutable release, Run, snapshot, and audit references
  require retained evidence.
- Letting the Builder browser infer model, Skill, MCP, or ACL defaults: those are
  server-authorized definition fields, not presentation defaults.
