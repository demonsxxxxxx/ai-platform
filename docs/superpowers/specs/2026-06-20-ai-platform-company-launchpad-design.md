# AI Platform Company Launchpad Design

Status: design approved for implementation planning
Date: 2026-06-20

## Goal

AI Platform should become the company-facing home page for internal AI and work
systems. The first version is a launchpad: users see the internal applications
and websites they already use, search them, and click through to the existing
systems.

This is not a migration of `nonGMPlimsUI/webUI` business modules into
AI Platform. `nonGMPlimsUI/webUI` remains a separate project and system of
record for its existing pages. AI Platform only provides an entry page that
links to those systems.

## Source Inventory

The initial application catalog is copied from the current
`nonGMPlimsUI/webUI` navigation data:

- `src/TaskManagement/config/lingxi-data.js`: Lingxi platform system entries,
  grouped by department.
- `src/TaskManagement/config/sites-data.js`: common company and external web
  links, grouped by category.
- `src/TaskManagement/config/ai-data.js`: AI tool entries, grouped by tool
  category.

Observed current clickable-entry inventory:

- Lingxi platform: 29 entries.
- Web navigation: 122 entries.
- AI applications: 4 entries.

## Scope

Phase 1 implements one AI Platform native React page, available from a stable
route such as `/apps`.

The page must support:

- Tabs for `灵犀平台`, `网页导航`, and `AI应用`.
- Search across all visible entries.
- Left-side group navigation for departments or categories.
- Clickable app/link cards with name, icon treatment, optional description, and
  destination behavior.
- Direct click-through to existing nonGMPlims, company intranet, or external
  systems.
- Clear disabled or "待接入" state only when a destination cannot be derived
  from existing data.

Phase 1 does not implement:

- Porting old Vue business pages into AI Platform.
- Rebuilding nonGMPlims permission routing in AI Platform.
- Embedding the whole old `indexSpace.vue` page in an iframe.
- Migrating old todo, calendar, workflow, dashboard, or statistics widgets.
- New backend application-catalog APIs.
- Department-level launchpad administration.

## Navigation Behavior

Each entry uses one of two destination modes:

1. `url`: open the existing URL in a new browser tab.
2. `systemKey`: resolve to a configured base URL or route for the existing
   nonGMPlims system, then open it in a new browser tab.

The AI Platform page must not assume ownership of the target system after the
click. Authentication, route permission, workflow state, and business behavior
remain the responsibility of the destination system.

If a `systemKey` cannot be mapped to a known destination in Phase 1, the card is
shown as unavailable with a short label such as `待接入`; clicking it should not
silently fail.

## Information Architecture

The launchpad page uses a restrained enterprise layout:

- Existing AI Platform sidebar/header shell stays intact.
- The main content area is a dense, scannable application directory.
- Top row contains page title, tab segmented control, and search input.
- Left column lists the groups in the active tab and scrolls to the selected
  group.
- Main column renders grouped cards.
- Cards use modest radius, subtle elevation, stable hover state, and vector icon
  treatments when old Element UI icon names cannot be reused directly.

The page should not use a marketing hero, decorative gradients, large
illustrations, nested cards, or one-off visual effects. It is a work homepage,
not a landing page.

## Data Shape

Create an AI Platform-local typed catalog from the old navigation data:

```ts
type LaunchpadTabKey = "lingxi" | "common" | "ai";

interface LaunchpadEntry {
  id: string;
  tab: LaunchpadTabKey;
  groupId: string;
  groupName: string;
  name: string;
  description?: string;
  icon?: string;
  color?: string;
  systemKey?: string;
  url?: string;
  unavailableReason?: string;
}
```

Destination resolution should be isolated from rendering so that Phase 2 can
replace static mappings with backend-managed catalogs without rewriting the UI.

## Styling

Use the existing AI Platform React/Tailwind conventions and the current
enterprise UI constraints:

- Background: quiet neutral app surface.
- Cards: white surface, radius no larger than 8px, subtle border or low-opacity
  shadow.
- Text: compact hierarchy with strong names and muted descriptions.
- Icons: use `lucide-react` or an existing icon system; do not use emojis as
  structural icons.
- Search and tabs: stable dimensions, no layout shift on focus or hover.
- Responsive behavior: desktop shows group navigation plus card grid; small
  screens collapse group navigation into a horizontal category strip or top
  select-like control.

## Permissions And Security

Phase 1 launchpad visibility is controlled by the existing AI Platform protected
route. It does not enforce target-system permissions beyond showing the link.
The destination system remains responsible for login and authorization.

Do not commit secrets, tokens, or real `.env` values. URLs copied from the old
navigation data are normal destination links, not credentials.

## Verification

Before implementation is considered ready:

- TypeScript build for `frontend/web` passes.
- The launchpad route renders behind login.
- Tabs switch without page reload.
- Search filters entries across the active catalog.
- At least one `url` entry opens the configured destination.
- At least one `systemKey` entry resolves to a nonGMPlims destination.
- Unknown mappings render a visible unavailable state.
- The page is visually checked on desktop and narrow viewport.

## Future Phase

Phase 2 can introduce backend-managed catalog APIs, department-scoped visibility,
admin editing, SSO-aware target URLs, audit logging for launchpad clicks, and
tenant-aware company homepage personalization.
