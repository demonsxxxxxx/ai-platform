# Role Governance Public API Contract

This contract covers the authenticated frontend `/roles` workbench surface. It
is separate from the legacy compatibility `/api/roles` endpoint and from direct
role CRUD administration.

## Auth And Permissions

All routes require an authenticated principal. Missing authentication returns
`401`. Missing authorization returns `403` with `detail` formatted as
`missing_permission:<permission>`.

Effective role-governance permissions are projected from the principal:

- `role:read` allows safe overview and request lookup.
- `role:request` allows ordinary users to queue access requests and implies
  `role:read`.
- `role:manage` implies `role:read` and `role:request`.
- platform admin roles receive all role-governance permissions.
- ordinary authenticated users with `role:request` can queue access requests; they
  cannot approve, reject, or rollback changes.

## Backed Routes

- `GET /api/role-governance/overview`
- `GET /api/role-governance/requests/{request_id}`
- `POST /api/role-governance/requests`
- `POST /api/role-governance/approvals/{request_id}/approve`
- `POST /api/role-governance/approvals/{request_id}/reject`
- `POST /api/role-governance/audit/{audit_id}/rollback`

`GET /api/role-governance/overview` returns:

- `governance`: tenant/workspace projection metadata, audit requirement, rollback
  availability, and `secret_material_projected=false`;
- `role_directory`: visible role IDs, display names, descriptions,
  requestability, assignability, scope, and capability labels;
- `scope`: current tenant, current workspace, current department, visible
  departments, visible workspaces, and inherited Skill availability;
- `requests`: safe request/approval workflow items from bounded tenant-scoped
  role-governance audit history, with a deterministic empty-state projection;
- `audit`: safe audit references from bounded tenant-scoped role-governance
  audit history with actor, timestamp, source, and rollback availability.

Rollback availability is projected from `role:manage`; ordinary `role:read` or
`role:request` users see rollback as unavailable.

The role directory intentionally does not expose raw permission names,
credential material, private payloads, source host paths, or full enterprise
permission catalogs.

## Write Semantics

`POST /api/role-governance/requests` accepts ordinary-user requests for
`target_type=role` or `target_type=department_agent`. It validates safe target
and workspace IDs against explicit requestable role and department-agent
allowlists, writes an audit log entry, and returns a queued
`WorkbenchOperationResponse`.

Approval, rejection, and rollback routes require `role:manage` or platform
admin. They queue audited operations and return stable operation metadata. They
do not directly grant a role, mutate enterprise RBAC, or bypass future
approval-worker policy.

Request bodies forbid extra fields. Secret-bearing fields such as
`private_payload`, `password`, token fields, and raw permission payloads are
rejected by schema or never projected. Free-text request reasons, decision
notes, and rollback reasons are sanitized before audit persistence; redacted
secret-like content is collapsed to `[redacted-private]`.

## Legacy Boundary

`/api/roles` remains a LambChat compatibility endpoint. It is not the product
authority for the `/roles` workbench and should not be used for new role
governance UI. Frontend migration should consume `/api/role-governance/*` for
role directory, department/workspace scope, request/approval workflow, and
audit/rollback state.

This backend slice is source/local contract evidence for issue 215. It does not
claim merged-main 211 deployment, broader readiness, or issue closure.
