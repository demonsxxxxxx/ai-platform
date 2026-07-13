# Company Login Two-Role RBAC V1 Design

Status: approved by the product owner in the revision-75 controller envelope.

## Goal

Company-login sessions expose exactly one product role, `admin` or `user`.
The backend owns the role and permission projection, old company sessions fail
closed after policy changes, and every frontend navigation and route surface
uses one access policy before rendering protected content. With no explicit
preference, the interface starts in Chinese.

## Root Cause

The current company-login path copies upstream role labels into the signed
session. It only falls back to `user` when the upstream role list is empty.
The ordinary permission baseline also includes inventory permissions for users,
roles, channels, settings, and feedback. On the frontend, most management routes
only require authentication; the rail, expanded/mobile sidebar, and user menu
build their entries independently. `ProtectedRoute.requireAdmin` infers admin
status from a small permission set instead of the signed `/auth/me` projection.
Finally, company cookies do not carry a code-owned authorization policy version,
so an old cookie can retain old roles and permissions until expiry.

## Backend Contract

### Canonical company roles

- Upstream `admin` or `developer`, matched case-insensitively after trimming,
  produces `roles=["admin"]`.
- A configured administrator match on either submitted login name or returned
  work ID produces `roles=["admin"]`.
- Every other upstream shape, including unknown, empty, malformed, or unavailable
  user information, produces `roles=["user"]`.
- Upstream company roles and permissions are never copied into the session.
- Trusted-header and other internal principals keep the existing platform role
  taxonomy and `is_ai_admin` compatibility aliases.

### Exact permission projection

The ordinary company permission set is:

```text
agent:use
chat:read
chat:write
session:read
session:write
skill:read
marketplace:read
mcp:read
persona_preset:read
avatar:upload
feedback:write
notification:read
artifact:download
file:upload
file:upload:document
```

The admin session receives that exact set plus:

```text
agent:read
agent:admin
model:admin
settings:read
settings:manage
settings:admin
admin:status
skill:write
skill:delete
skill:admin
marketplace:publish
marketplace:admin
mcp:write_sse
mcp:write_http
mcp:write_sandbox
mcp:delete
mcp:admin
persona_preset:write
persona_preset:admin
channel:read
channel:write
channel:delete
channel:admin
user:read
user:write
user:delete
user:admin
role:read
role:manage
feedback:read
feedback:admin
notification:admin
notification:manage
```

Tests compare exact ordered sets so future additions require an explicit policy
decision.

### Company session policy version

`COMPANY_AUTHZ_POLICY_VERSION` is a code constant in `app/auth.py`. Signing a
`source="company-login"` principal writes the version into the payload. Verifying
a company session rejects a missing, non-integer, or mismatched version with
HTTP 401 `stale_company_session`. Non-company signed principals remain compatible.
No secret or database setting changes are required.

## Frontend Contract

`workbenchAccessPolicy.ts` is a pure module. It accepts the `/auth/me` user
projection and an access key or pathname. `user.is_admin === true` is the only
admin fact; permissions remain capability checks within allowed pages, not an
admin identity heuristic.

Admin-only destinations are `/users`, `/roles`, `/settings`, `/channels`,
`/agents`, `/models`, and `/feedback`. Ordinary users retain `/chat`, `/apps`,
`/skills`, `/mcp`, `/persona`, `/files`, `/agent-workspace`, `/notifications`,
and `/memory`. The rail, expanded/mobile sidebar, user menu, and direct routes
reuse the same module. Direct unauthorized navigation returns `<Navigate
to="/chat" replace />` from `ProtectedRoute` before page children render.

Role presentation only displays the canonical role code in compact navigation
and uses the existing `workbench.governance.roleLabels.admin/user` locale keys
in the profile.

## Language Contract

An explicit saved language remains first priority. Backend metadata continues to
update that saved preference through `useAuth`. With no saved preference, SSR,
browser startup, and i18next fallback use `zh`; browser locale is not an implicit
preference. Locale JSON files are owned by the MCP lane and are not modified.

## UX Direction

Preserve the existing quiet, dense enterprise workbench and its current tokens,
icons, geometry, focus states, and responsive shell. This change introduces no
new visual system. Its signature behavior is absence: unauthorized destinations
never enter the navigation tree and protected page content never flashes. At
1440x900 and 390x844, controls retain stable dimensions, Chinese labels wrap or
truncate within their existing containers, and no duplicate navigation hierarchy
is introduced.

## Verification

- Backend unit and route tests use synthetic identities only.
- Frontend pure-policy and source-contract tests cover ordinary/admin matrices,
  route redirects, navigation consumers, role labels, and language defaults.
- Browser smoke mocks `/auth/me` and public projections for synthetic `admin`
  and `user` roles at desktop and mobile viewports.
- Compile, lint, typecheck, build, projection audit, exact-head security review,
  and exact-head UX review are required before the ready PR.
- No real credentials, Docker, deployment, merge, or 211 access is permitted.
