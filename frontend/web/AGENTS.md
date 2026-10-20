# Working in the web frontend

Apply the root [repository instructions](../../AGENTS.md). This file is the
frontend implementation and verification entry; detailed contracts stay with
the owners linked below.

## Components and page styling

- Use Lobe UI as the primary general React UI library. Follow the
  [integration contract](../../docs/frontend/lobe-ui-adoption.md) for versions,
  public imports, providers and component behavior. Do not add a parallel UI
  library, private imports or same-name wrappers without a product responsibility.
- AI chat prioritizes the pinned assistant-ui Elements source for thread/message
  framing, composer, attachments, activities, lists and question receipts. Follow the
  [chat integration contract](../../docs/frontend/assistant-ui-chat-adoption.md)
  for source provenance, version pins and retained business adapters.
  Compose Lobe general controls inside its public slots; do not add a second
  chat runtime or use Lobe chat components as a parallel interaction owner.
  Keep the platform SSE data chain; consume one runtime-bound message snapshot
  for text and business parts, with no parallel generic text/loading renderer.
- Follow the [visual guidelines](../../docs/frontend/lobehub-visual-guidelines.md)
  for hierarchy, spacing, typography, themes and responsive layout. Keep page CSS
  scoped to its owner; use public component sizing APIs instead of global
  button/input/svg overrides. Style-preview approval and actual-page acceptance
  are separate.
- Follow the [table and Select contract](../../docs/frontend/shared-table-select-components.md)
  for business state, renderer exceptions and retired controls. Preserve field
  values, labels, submit/disabled/loading behavior, keyboard access and focus.

## Keep product authority with its owner

- Place code by its [stable responsibility](../../docs/architecture/source-code-architecture.md).
  Frontend composition and layout do not redefine authentication, permissions,
  tenant/workspace binding, Profile publication, Run/SSE state or public projections.
  assistant-ui consumes the platform adapter; submission receipts, durable drafts,
  uploads, file authorization and session identity remain with their existing owners.
- Keep expert access editing department-only and preserve historical API fields,
  as specified by the table and Select contract.
- Theme and sidebar state use the existing
  [browser preference owners](../../docs/frontend/browser-local-preferences.md);
  do not introduce another store or profile-metadata write for them.
- Development previews use synthetic local state and stay outside production
  routes and authentication/model/API flows, per the visual guidelines.

## Complete the affected checks

- Before changing a control, dependency or test command, inventory its callers,
  TypeScript/DOM tests, root Python frontend contracts, browser selectors,
  package/lock files and owning documentation. Update obsolete assertions with
  the implementation; preserve required coverage and retirement inventories.
- [Package scripts](package.json) and the
  [frontend workflow](../../.github/workflows/ai-platform-frontend.yml) own the
  executable check list. Script/loader/selector changes must also run the affected
  root Python contracts through the
  [local test runner](../../docs/agent-rules/local-test-execution.md). Passing
  selected pnpm stages does not establish a full CI pass.
- Mounted control tests use the real Lobe providers and shared test setup from
  the integration contract. For changed layouts, inspect actual rendered pages
  in light/dark themes at desktop and 390px width; exercise the affected keyboard,
  focus, portal and table-scroll behavior with synthetic fixtures. Source
  inventory and JSDOM checks do not establish visual acceptance.
- Report checks against the actual candidate, distinguish synthetic browser
  evidence from real services, and retain failures or unrun checks in the PR.
