# Skills And Marketplace Public API Contract

This contract covers the authenticated frontend Skills and Marketplace surfaces. It is separate from the admin release-management API under `/api/ai/admin/skills/*`.

## Auth And Permissions

All routes require an authenticated principal. Missing authentication returns `401`. Missing authorization returns `403` with `detail` formatted as `missing_permission:<permission>`.

MCP lifecycle routes are a role-gated exception in this first backend slice:
non-admin principals receive `403 not_ai_admin`, and platform admins receive
`409 mcp_lifecycle_contract_not_backed` until lifecycle governance is backed.

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

Explicitly fail-closed routes:

- `POST /api/github/preview` returns `409 skill_import_contract_not_backed` after `skill:write` passes.
- `POST /api/github/install` returns `409 skill_import_contract_not_backed` after `skill:write` passes.

Those GitHub import routes are present so the frontend gets an authenticated contract instead of `404`; GitHub network import storage remains a later backend slice.

## Marketplace Routes

Backed routes:

- `GET /api/marketplace/`
- `GET /api/marketplace/tags`
- `GET /api/marketplace/{skill_name}`
- `GET /api/marketplace/{skill_name}/files`
- `GET /api/marketplace/{skill_name}/files/{file_path}`
- `POST /api/marketplace/{skill_name}/install`
- `POST /api/marketplace/{skill_name}/update`

Marketplace list/detail/files are projected only from globally active public workbench skills. Tenant-disabled skills remain visible in the marketplace projection so users with `skill:write` can install/update them back to active. Internal dependencies are not exposed as ordinary marketplace entries.

`install` and `update` enable the selected public skill in tenant availability and write audit evidence. They do not expose package upload, release promote, rollback, MCP lifecycle, or tool execution controls to ordinary users.

Explicitly fail-closed direct marketplace write routes:

- `POST /api/marketplace/`
- `PUT /api/marketplace/{skill_name}`
- `PATCH /api/marketplace/{skill_name}/activate`
- `DELETE /api/marketplace/{skill_name}`

Those direct marketplace lifecycle routes return `409 marketplace_direct_write_contract_not_backed` after `marketplace:admin` passes. The backed public path remains publish-request audit plus admin release management under `/api/ai/admin/skills/*`.

## MCP Routes

Backed read routes:

- `GET /api/mcp/`
- `GET /api/mcp/{name}`
- `GET /api/mcp/{name}/tools`
- `GET /api/mcp/export`

The MCP read projection is built from platform-registered MCP tools and tenant tool policies. It exposes governed server/tool directory metadata for frontend discovery without raw credentials, server headers, runtime paths, or unmanaged lifecycle controls.

Explicitly fail-closed lifecycle routes:

- `POST /api/mcp/`
- `PUT /api/mcp/{name}`
- `DELETE /api/mcp/{name}`
- `PATCH /api/mcp/{name}/toggle`
- `POST /api/mcp/import`
- `PATCH /api/mcp/{name}/tools/{tool_name}`
- `POST /api/admin/mcp/`
- `PUT /api/admin/mcp/{name}`
- `DELETE /api/admin/mcp/{name}`
- `POST /api/admin/mcp/{name}/promote`
- `POST /api/admin/mcp/{name}/demote`

Those lifecycle routes require platform admin and then return `409 mcp_lifecycle_contract_not_backed`. Tool policy writes remain under `/api/ai/admin/tool-policies/*`; ordinary users do not gain MCP server CRUD, credential lifecycle, or write-tool bypass authority from this public route set.
