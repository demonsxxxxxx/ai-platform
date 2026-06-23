# Skills And Marketplace Public API Contract

This contract covers the authenticated frontend Skills and Marketplace surfaces. It is separate from the admin release-management API under `/api/ai/admin/skills/*`.

## Auth And Permissions

All routes require an authenticated principal. Missing authentication returns `401`. Missing authorization returns `403` with `detail` formatted as `missing_permission:<permission>`.

MCP lifecycle routes are platform-admin gated. Server registry create, update,
delete, and enablement now persist tenant-scoped lifecycle metadata with
redacted credential evidence; remaining import, tool-toggle, promote, and
demote flows still return `409 mcp_lifecycle_contract_not_backed` until their
governance paths are backed.

Effective permissions are projected from the principal permissions plus admin role expansion:

- `skill:admin` implies `skill:read`, `skill:write`, and `skill:delete`.
- `marketplace:admin` implies `marketplace:read` and `marketplace:publish`.
- platform admin roles receive all Skills and Marketplace public permissions.

Company-login and `/api/auth/*` compatibility projections now include:

- ordinary user discovery: `skill:read`, `marketplace:read`;
- admin/publisher actions: `skill:write`, `skill:delete`, `skill:admin`, `marketplace:publish`, `marketplace:admin`.

## Skills Routes

Backed routes:

- `GET /api/skills/`
- `GET /api/skills/{skill_name}`
- `GET /api/skills/{skill_name}/files/{file_path}`
- `PUT /api/skills/{skill_name}/files/{file_path}`
- `DELETE /api/skills/{skill_name}/files/{file_path}`
- `POST /api/skills/upload/preview`
- `POST /api/skills/upload`
- `PATCH /api/skills/{skill_name}/toggle`
- `DELETE /api/skills/{skill_name}`
- `POST /api/skills/batch/delete`
- `POST /api/skills/batch/toggle`
- `POST /api/skills/{skill_name}/publish`

`GET /api/skills/` returns the frontend list contract with `skills`, `total`, `skip`, `limit`, `available_tags`, and `effective_permissions`. Catalog data is projected from public workbench skills, tenant availability, and the effective skill version snapshot.

`PATCH /api/skills/{skill_name}/toggle` maps to tenant skill availability in `tenant_workbench_skills`; it does not invoke admin promote or rollback.

`POST /api/skills/{skill_name}/publish` records a public publish request audit and returns the marketplace projection for the skill. It does not substitute for `/api/ai/admin/skills/{skill_id}/promote`.

`POST /api/skills/batch/delete` and `POST /api/skills/batch/toggle` map to tenant skill availability and audit each affected skill. Batch delete disables tenant availability; it does not delete global built-in Skill packages or admin release records.

PUT `/api/skills/{skill_name}/files/{file_path}` stores a tenant/user-scoped UTF-8 text file overlay after `skill:write` passes. The overlay is audited, size-limited by backend configuration, and appears only in that user's public Skills projection. Binary/base64 asset overlays remain out of scope until the import storage slice is backed.

DELETE `/api/skills/{skill_name}/files/{file_path}` stores a tenant/user-scoped tombstone after `skill:delete` passes. The tombstone hides the file from that user's public Skills projection without deleting the released Skill snapshot.

Marketplace file previews continue to read released Skill snapshots and do not include tenant/user file overlays.

`POST /api/skills/upload/preview` accepts a multipart ZIP package in field
`file`, validates the package `SKILL.md`, and returns package metadata without
persistence. It only supports one Skill package per ZIP in this backend slice.

`POST /api/skills/upload` accepts the same package shape for an existing public
Skill and persists the package files as tenant/user-scoped public Skill file
overlays after `skill:write` passes. It enables tenant availability and writes
audit evidence. It does not create a global built-in Skill, direct Marketplace
entry, admin Skill version, or release-policy promotion.

`POST /api/github/preview` accepts a public `https://github.com/{owner}/{repo}`
repository URL and branch, downloads the GitHub ZIP archive, discovers Skill
packages under directories containing `SKILL.md`, and returns `repo_url`,
`branch`, and `skills[{name,path,description}]` without persistence.

`POST /api/github/install` accepts the same public GitHub source plus selected
`skill_names` and persists matching existing public Skill packages as
tenant/user-scoped public Skill file overlays. It enables tenant availability
and writes audit evidence. It does not support private GitHub tokens, arbitrary
Git hosts, new global built-in Skill creation, direct Marketplace entry
creation, admin Skill versions, or release-policy promotion.

## Marketplace Routes

Backed routes:

- `GET /api/marketplace/`
- `GET /api/marketplace/tags`
- `GET /api/marketplace/{skill_name}`
- `GET /api/marketplace/{skill_name}/files`
- `GET /api/marketplace/{skill_name}/files/{file_path}`
- `POST /api/marketplace/{skill_name}/install`
- `POST /api/marketplace/{skill_name}/update`
- `POST /api/marketplace/`
- `PUT /api/marketplace/{skill_name}`
- `PATCH /api/marketplace/{skill_name}/activate`
- `DELETE /api/marketplace/{skill_name}`

Marketplace list/detail/files are projected only from globally active public workbench skills. Tenant-disabled skills remain visible in the marketplace projection so users with `skill:write` can install/update them back to active. Internal dependencies are not exposed as ordinary marketplace entries.

`install` and `update` enable the selected public skill in tenant availability and write audit evidence. They do not expose package upload, release promote, rollback, MCP lifecycle, or tool execution controls to ordinary users.

Direct marketplace lifecycle routes are backed for authorized marketplace admins:

- `POST /api/marketplace/` and `PUT /api/marketplace/{skill_name}` materialize tenant-facing Skill metadata as an immutable `skill_versions` snapshot and point the tenant stable release policy at that snapshot. They do not mutate the global `skills` catalog row.
- `PATCH /api/marketplace/{skill_name}/activate` accepts either `active` or the frontend-compatible `is_active` body field and updates tenant availability.
- `DELETE /api/marketplace/{skill_name}` disables tenant Marketplace availability without deleting global Skill records.

Package upload, rollback, and low-level release management remain under the admin release-management surface at `/api/ai/admin/skills/*`.

## MCP Routes

Backed read and server lifecycle routes:

- `GET /api/mcp/`
- `GET /api/mcp/{name}`
- `GET /api/mcp/{name}/tools`
- `GET /api/mcp/export`
- `POST /api/mcp/`
- `PUT /api/mcp/{name}`
- `DELETE /api/mcp/{name}`
- `PATCH /api/mcp/{name}/toggle`
- `POST /api/admin/mcp/`
- `PUT /api/admin/mcp/{name}`
- `DELETE /api/admin/mcp/{name}`

The MCP read projection is built from the tenant MCP server registry and falls
back to platform-registered MCP tools plus tenant tool policies for seeded
tools. It exposes governed server/tool directory metadata for frontend
discovery without raw credentials, server headers, runtime paths, or unmanaged
tool execution controls.

Server lifecycle writes require a platform-admin principal. They persist only
tenant-scoped registry metadata, redacted endpoint shape, allowed roles,
department enablement, quotas, credential state, credential metadata such as
header names or env key names, and a credential fingerprint. Raw URL query
secrets, header values, commands, and credential values are not returned in API
responses and are not written to audit payloads.

Explicitly fail-closed follow-up routes:

- `POST /api/mcp/import`
- `PATCH /api/mcp/{name}/tools/{tool_name}`
- `POST /api/admin/mcp/{name}/promote`
- `POST /api/admin/mcp/{name}/demote`

Those follow-up routes require platform admin and then return
`409 mcp_lifecycle_contract_not_backed`. Tool policy writes remain under
`/api/ai/admin/tool-policies/*`; ordinary users do not gain MCP server CRUD,
credential lifecycle, or write-tool bypass authority from this public route set.

## Tool Permission Approval Inbox

Backed current-user approval inbox routes:

- `GET /api/ai/tool-permissions/inbox`
- `POST /api/ai/tool-permissions/inbox/{request_id}/decision`

The inbox is a durable projection of `run_tool_permission_requests` for the
authenticated tenant/user. `status=pending`, `status=decided`, and `status=all`
filter the current user's requests across runs without granting access to other
users, other tenants, or raw executor payloads. Inbox decisions reuse the same
exact run/request decision writer, event, audit, expiry, and replay-denial
semantics as `/api/ai/runs/{run_id}/tool-permissions/{request_id}/decision`.
