# Skills And Marketplace Public API Contract

This contract covers the authenticated frontend Skills and Marketplace surfaces. It is separate from the admin release-management API under `/api/ai/admin/skills/*`.

## Auth And Permissions

All routes require an authenticated principal. Missing authentication returns `401`. Missing authorization returns `403` with `detail` formatted as `missing_permission:<permission>`.

MCP lifecycle routes are platform-admin gated. Server registry create, update,
delete, and enablement persist tenant-scoped lifecycle metadata with redacted
credential evidence. The former compatibility-only routes `POST /api/mcp/import`,
`PATCH /api/mcp/{name}/tools/{tool_name}`, `POST /api/admin/mcp/{name}/promote`,
and `POST /api/admin/mcp/{name}/demote` are retired and absent; tool discovery
and backed server lifecycle routes remain listed below.

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

`GET /api/skills/` returns the frontend list contract with `skills`, `total`, `skip`, `limit`, `available_tags`, and `effective_permissions`. Catalog data is projected from public workbench skills, tenant availability, and the effective skill version snapshot.

`PATCH /api/skills/{skill_name}/toggle` maps to tenant skill availability in `tenant_workbench_skills`; it does not invoke admin promote or rollback.

No public `/api/skills/{skill_name}/publish` route is backed. Global Skill release remains exclusively under the Admin review, materialization, promote, and rollback lifecycle at `/api/ai/admin/skills/*`.

Admin ZIP uploads retain the content hash as the immutable release and execution
identity. The admin catalog separately projects a human-readable upload version:
the first uploaded package is `1.0.0`, and each later package for the same Skill
increments the patch component. Legacy uploaded packages receive the same
creation-order projection without rewriting their immutable records. The admin
catalog also exposes `latest_uploaded_at` from the immutable latest uploaded
version's creation timestamp (or `null` for built-in-only Skills). The management
list uses it for update time before falling back to the public runtime timestamp;
reusing identical package content does not create a new timestamp.

`POST /api/skills/batch/delete` and `POST /api/skills/batch/toggle` map to tenant skill availability and audit each affected skill. Batch delete disables tenant availability; it does not delete global built-in Skill packages or admin release records.

PUT `/api/skills/{skill_name}/files/{file_path}` stores a tenant/user-scoped UTF-8 text file overlay after `skill:write` passes. The overlay is audited, size-limited by backend configuration, and appears only in that user's public Skills projection. Binary/base64 asset overlays remain out of scope until the import storage slice is backed.

DELETE `/api/skills/{skill_name}/files/{file_path}` stores a tenant/user-scoped tombstone after `skill:delete` passes. The tombstone hides the file from that user's public Skills projection without deleting the released Skill snapshot.

Marketplace file previews continue to read released Skill snapshots and do not include tenant/user file overlays.

`POST /api/skills/upload/preview` accepts a multipart ZIP package in field
`file`, validates the package `SKILL.md`, and returns package metadata without
persistence. It only supports one Skill package per ZIP in this backend slice.
Preview and actual upload use the same package parser: decoded file and directory
name components must fit within 255 UTF-8 bytes; non-ASCII ZIP names without
a UTF-8 flag or a verified Unicode Path (0x7075) extra field are rejected rather than
guessed or silently renamed. ASCII names need no encoding flag. The admin
Skill package preview and upload follow the same package-shape validation.

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
- `PATCH /api/marketplace/{skill_name}/activate`
- `DELETE /api/marketplace/{skill_name}`

Marketplace list/detail/files are projected only from globally active public workbench skills. Tenant-disabled skills remain visible in the marketplace projection so users with `skill:write` can install/update them back to active. Internal dependencies are not exposed as ordinary marketplace entries.

`install` and `update` enable the selected public skill in tenant availability and write audit evidence. They do not expose package upload, release promote, rollback, MCP lifecycle, or tool execution controls to ordinary users.

Tenant Marketplace distribution lifecycle routes are backed for authorized marketplace admins:

- `PATCH /api/marketplace/{skill_name}/activate` accepts either `active` or the frontend-compatible `is_active` body field and updates tenant availability.
- `DELETE /api/marketplace/{skill_name}` disables tenant Marketplace availability without deleting global Skill records.

The Admin release-management surface under `/api/ai/admin/skills/*` is the only
authority for immutable version upload, review, promote, rollout policy, and
rollback. Marketplace routes remain projections and tenant-distribution
controls; they cannot create an active version or redirect a release policy.

`GET /api/ai/admin/skills` and its detail route expose the current management
catalog only. Every returned aggregate has `lifecycle_status: "active"`;
distribution and version lifecycle remain separate fields. Retired global rows
stay in PostgreSQL for historical Run, Session, snapshot, and audit references,
while the management catalog excludes them. Built-in synchronization is bounded
to Skills classified as public workbench capabilities or internal dependencies,
so historical synthetic identities such as `general-chat` cannot be republished.

## MCP Routes

Backed read and server lifecycle routes:

- `GET /api/mcp/chat-tools`
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

`GET /api/mcp/chat-tools` discovers each user's current effective tools from
every distributed MCP Server by calling `tools/list` with that user's
server-side company JWT. Dynamic Gateway tool definitions and ACLs are not
persisted or cached in AI Platform; every request performs a fresh discovery,
while each Gateway owns its internal catalog and ACL caching. A single
unavailable Server does not hide tools from other Servers. Responses expose
stable `mcp_server_id::public_tool_name`
references and bounded display metadata, never JWTs, static headers, Gateway
internal IDs, or cache keys. The code-owned RAGFlow row remains the only local
`mcp_tools` compatibility entry while its built-in dependency is retained.

Server lifecycle writes require a platform-admin principal. They persist only
tenant-scoped registry metadata, allowed roles, department enablement, quotas,
credential state, bounded metadata, and a credential fingerprint. Endpoint and
static headers are stored only in a tenant/Server-bound encrypted envelope;
static headers cannot use the reserved `JWT-Authorization` name. Raw URL query
secrets, header names or values, commands, JWTs, and credential values are not
returned in API responses or written to audit payloads.

Company login stores one encrypted MCP JWT per `tenant_id + user_id` in Redis;
the JWT's own `exp` is its lifetime and a later login replaces the earlier
value. Ordinary MCP flows never return this JWT to the browser. Two bounded
same-origin integrations use `POST /api/ai/auth/company-credential-handoff`,
which returns the current user's JWT to an authenticated page with
`Cache-Control: private, no-store`:

- the document translator keeps it only in memory, validates the child window
  and nonce, and sends it to the fixed translator origin; the translator keeps
  it in tab-scoped `sessionStorage` for its API calls;
- the administrator-only ProfileDrive marketplace page keeps it only for one
  request to the fixed same-origin `/api/profile-drive/` proxy. That proxy
  strips the AI Platform session cookie and forwards the JWT as
  `Authorization` to the configured connector.

Neither flow places the JWT in a URL or AI Platform browser storage. At MCP
execution time the Worker reuses the existing Capability Distribution and Tool
Policy plan and reads the current JWT and encrypted Server target. The executor
opens remote MCP sessions with static headers plus `JWT-Authorization`, then
exposes only the authorized selected tools through the SDK's in-process MCP
interface. For ProfileDrive, an authorized `read_text_file` selection is mapped
inside the governed sandbox to `stage_profile_drive_file_to_workspace`: the
attempt-bound platform callback reads the current user's JWT, streams at most
512 MiB from the fixed HTTPS connector upstream, and atomically places the file
under the Run workspace. The tool result contains only the workspace-relative
path and byte count; SMB credentials and file bytes never enter model messages.
The connector's existing JSON `read_text_file` tool remains available only as a
compatibility surface for non-AI-Platform consumers and is not exposed alongside
the workspace staging tool in sandbox Runs. Other SDK calls pass through the
adapter to their original remote tool names. There is no separate generic MCP
Broker capability or model-selected upstream URL, and runtime connection
material is removed from reconciliation persistence.

The [MCP execution contract](../architecture/mcp-tool-execution.md) owns selected-tool exposure, SDK alias mapping, HTTP/SSE transport limits, and runnable acceptance. Command/stdin (`sandbox`) configuration writes are rejected until a governed process adapter exists; existing rows remain readable but do not authorize command execution. Ordinary directory responses with `unavailable_reason` display unavailable state rather than an empty successful catalog.

Tool policy writes remain under `/api/ai/admin/tool-policies/*`; ordinary users
do not gain MCP server CRUD, credential lifecycle, or write-tool bypass
authority from this public route set.
