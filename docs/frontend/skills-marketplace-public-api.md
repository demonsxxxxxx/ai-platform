# Skills And Marketplace Public API Contract

This contract covers the authenticated frontend Skills and Marketplace surfaces. It is separate from the admin release-management API under `/api/ai/admin/skills/*`.

## Auth And Permissions

All routes require an authenticated principal. Missing authentication returns `401`. Missing authorization returns `403` with `detail` formatted as `missing_permission:<permission>`.

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
- `PATCH /api/skills/{skill_name}/toggle`
- `DELETE /api/skills/{skill_name}`
- `POST /api/skills/{skill_name}/publish`

`GET /api/skills/` returns the frontend list contract with `skills`, `total`, `skip`, `limit`, `available_tags`, and `effective_permissions`. Catalog data is projected from public workbench skills, tenant availability, and the effective skill version snapshot.

`PATCH /api/skills/{skill_name}/toggle` maps to tenant skill availability in `tenant_workbench_skills`; it does not invoke admin promote or rollback.

`POST /api/skills/{skill_name}/publish` records a public publish request audit and returns the marketplace projection for the skill. It does not substitute for `/api/ai/admin/skills/{skill_id}/promote`.

Explicitly fail-closed routes:

- `PUT /api/skills/{skill_name}/files/{file_path}` returns `409 skill_file_write_contract_not_backed` after `skill:write` passes.
- `DELETE /api/skills/{skill_name}/files/{file_path}` returns `409 skill_file_delete_contract_not_backed` after `skill:delete` passes.

Those file-write routes are present so the frontend gets an authenticated contract instead of `404`; durable per-user skill file storage remains a later backend slice.

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
