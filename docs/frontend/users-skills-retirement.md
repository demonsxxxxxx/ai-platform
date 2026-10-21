# Users Compatibility and Skills Retirement

This is the active contract for the legacy Workbench Users surface. Real
administrator diagnostics remain owned by Identity at `/api/ai/admin/users` and
`/api/ai/admin/users/{user_id}/diagnostics`; the current Users page uses those
readers. This change does not introduce user provisioning or change that page.

## Legacy Users HTTP boundary

`GET /api/users/` remains an authenticated-self compatibility view, not a company
directory. Its projection is `authenticated_self`, with `degraded: true` for all
roles. Search filters that one principal and pagination slices the filtered
result; `total` is the filtered self count before pagination. Negative offsets
and limits outside 1–100 are rejected. `GET /api/users/{user_id}` returns the
principal only when the ID matches. An ordinary caller requesting another ID
keeps the existing `403 missing_permission:user:admin`; a platform administrator
receives `501 user_directory_unavailable`. No caller receives their own roles or
profile relabelled as somebody else.

Authenticated and authorized `POST /api/users/`, `PUT /api/users/{user_id}` and
`DELETE /api/users/{user_id}` return exactly
`{"detail": "user_management_unavailable"}` with status 501. These operations
have no lifecycle implementation. They do not consume bodies as commands,
open transactions, append audit rows, enqueue work, or claim success. Existing
authentication, deployment-tenant enforcement, admin-permission implications and
platform-admin override are retained. Targeted writes validate safe IDs after
permission checks, returning 400 for invalid IDs. The unsupported write schema
is removed from OpenAPI instead of advertising a success response.

The compatibility owner is the Workbench route boundary. External consumers of
these old URLs have not been inventoried; therefore the URLs are retained with
truthful bounded behavior. Removing the URLs entirely requires an explicit
consumer migration decision. A future directory or provisioning implementation
must use the Identity owner and real tenant-scoped persistence, with new
acceptance coverage. Audit-request behavior in Role Governance is separate.

## Retired sources and callers

- Remove the audit-only `_record_operation` implementation and the unused
  `WorkbenchUserWriteRequest` DTO. Keep shared governance and operation response
  DTOs used by Role Governance.
- Remove the unconsumed frontend `workbenchApi.listUsers` client, its user DTOs
  and barrel export. The combined retirement also removes the final unused
  `WorkbenchGovernance` DTO and its module. Active Role Governance retains its
  separate `RoleGovernanceWorkbenchGovernance` contract.
- Update the RBAC browser smoke's synthetic directory response to the actual
  admin diagnostics API; this fixture is not production acceptance evidence.
- Remove `app/skills/infrastructure/legacy_workbench.py` and
  `tests/test_skill_legacy_workbench.py`. The two functions only raised retired
  catalog errors and had no production consumers in the fixed-source text
  inventory. Public Skill and Marketplace routes remain unchanged, including
  their authorization, missing-resource and OpenAPI coverage.
- Remove the retired MCP block preview's final unconsumed `toolUtils.ts`
  helper; the active attachment/reveal preview, ToolResultPanel and sidebar
  history owners remain.
- Remove the deleted SkillCard's unconsumed `cardUtils.ts`/`getCategoryIcon`
  and AvailableSkillsPanel's skill-only ordinary catalog projector and stale
  tests. Active MCP projection stays; real SkillsCatalogPublicHost tests own
  ordinary Skill metadata, redaction and keyboard coverage.
- Remove the retired help menu's unconsumed `GITHUB_URL` export.
- Preserve historical evidence as history. The generated source inventory is a
  static measurement rather than proof of semantic review or test coverage.

## Verification

Use the repository test-stage runner for Workbench route regressions, Identity
administrator diagnostics and Skill/Marketplace routes. Regressions must falsify
the old fake-queued and arbitrary-ID behavior, preserve self reads, enforce
permission and tenant boundaries, reject unsafe IDs, and verify unsupported
writes do not advertise HTTP success in OpenAPI. Scan repository source for the
retired client, DTO and Skills symbols, distinguishing historical documentation
from executable consumers. A text inventory does not constitute full-file
semantic review, and focused tests do not establish full-suite acceptance.
