# Frontend Complete Backend Gap Summary

Date: 2026-06-23
Branch: `codex/frontend-complete-pass-20260623`
Status: `PR ready` frontend evidence; backend follow-up remains open

## Confirmed Backed Contracts

- PR #177 is merged and this branch contains the merge commit for `[codex] add public skills marketplace contracts`.
- Public Skills read contracts are backed under `/api/skills/`: list, detail, file read, tenant availability toggle, tenant availability delete, and publish-request audit.
- Marketplace read and install/update contracts are backed under `/api/marketplace/`: list, tags, detail, files, install, and update.
- Tool permission request/decision contracts are backed under `/api/ai/runs/{run_id}/tool-permissions/...`.
- Admin tool policy inventory, history, and update contracts are backed under `/api/ai/admin/tool-policies`.
- Public model catalog reads are backed under `/api/agent/models/available`; this is sufficient for a read-only model catalog surface.
- PR #187 is merged and adds the first backend contract slice for Skills import/batch routes, direct Marketplace lifecycle routes, MCP read projection, and MCP lifecycle/admin route surfaces. These new contracts are permission-gated and intentionally fail closed where durable storage or lifecycle implementation is still not backed.

## Remaining Backend Gaps

- Durable public Skill file writes remain intentionally fail-closed. `PUT /api/skills/{skill_name}/files/{file_path}` and `DELETE /api/skills/{skill_name}/files/{file_path}` return `409` until user skill storage is backed.
- Skills ZIP/GitHub import storage is not durable yet. PR #187 adds stable fail-closed contracts, not actual import storage.
- Direct Marketplace write/admin lifecycle remains fail-closed behind backend policy until product scope and storage are complete.
- MCP tool governance is partially backed by admin tool policies, run-scoped tool permission decisions, and PR #187 read projections, but not by real server CRUD, credential lifecycle, department enablement, or a standalone approval inbox.
- Model provider list projection is absent on 211: `/api/agent/models/providers/list` returned `404` while `/api/agent/models/available` returned `200`. The frontend derives provider counts from the public model catalog and shows only a small degraded provider-projection notice.

## 211 Runtime Notes

- 211 static frontend provenance was refreshed after the #187 backend runtime deployment.
- The 211 backend API/worker currently run `ai-platform:df85a9f-issue183-contracts-runtime-only-v1`, with runtime labels pointing at merged main commit `df85a9fb3266aab92a2ca4122db06d4ec7a00175`.
- Post-deployment unauthenticated route probes no longer return 404 for the #187 route surfaces: `GET /api/skills/upload/preview` returned `405`, `GET /api/mcp/` returned `401`, and `GET /api/admin/mcp/` returned `405`. The backend issue has a fuller authenticated route smoke comment for the first #183 backend contract slice.
- Do not treat #183 as `gate closable`: durable user Skill storage, ZIP/GitHub import storage, direct Marketplace lifecycle, and real MCP server CRUD/credential/department/approval governance remain open.

## Frontend Handling

- Marketplace keeps read, preview, install, and update affordances enabled only when `marketplace:read` and `skill:write` allow them.
- Marketplace direct create/edit/activate/delete affordances stay hidden behind `marketplaceDirectWriteBacked = false`.
- Skills catalog keeps read, toggle, delete, publish-request, and export paths visible when authorized.
- Skills create, edit, ZIP import, GitHub import, and batch actions stay hidden until durable file storage/import/batch routes exist.
- MCP page stays as a governed directory shell with lifecycle and credential controls shown as fail-closed, not as writable controls.
- Models page uses `/api/agent/models/available` as the source of truth. If `/api/agent/models/providers/list` is absent, provider summaries are derived from the returned models instead of re-enabling the legacy model admin page.

## Backend Follow-Up Needed

Filed backend follow-up: https://github.com/demonsxxxxxx/ai-platform/issues/183

Issue #183 covers:

- Durable user Skill write storage for file create/update/delete.
- Skills ZIP/GitHub import preview and install contracts, or frontend removal of those legacy paths from the product scope.
- Batch toggle/delete contracts, or explicit product decision to remove batch management.
- Marketplace direct publish/edit/admin lifecycle contracts, if those actions are intended beyond publish-request audit.
- MCP server lifecycle, credential governance, department enablement, and approval inbox contracts.
- Optional model provider list projection, if the frontend should display backend-authored provider protocol and prefix metadata instead of deriving it from model values.
