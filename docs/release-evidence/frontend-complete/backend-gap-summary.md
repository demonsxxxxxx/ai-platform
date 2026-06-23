# Frontend Complete Backend Gap Summary

Date: 2026-06-23
Branch: `codex/frontend-complete-pass-20260623`
Status: `local partial`

## Confirmed Backed Contracts

- PR #177 is merged and this branch contains the merge commit for `[codex] add public skills marketplace contracts`.
- Public Skills read contracts are backed under `/api/skills/`: list, detail, file read, tenant availability toggle, tenant availability delete, and publish-request audit.
- Marketplace read and install/update contracts are backed under `/api/marketplace/`: list, tags, detail, files, install, and update.
- Tool permission request/decision contracts are backed under `/api/ai/runs/{run_id}/tool-permissions/...`.
- Admin tool policy inventory, history, and update contracts are backed under `/api/ai/admin/tool-policies`.

## Remaining Backend Gaps

- Durable public Skill file writes remain intentionally fail-closed. `PUT /api/skills/{skill_name}/files/{file_path}` and `DELETE /api/skills/{skill_name}/files/{file_path}` return `409` until user skill storage is backed.
- Skills import and batch-management routes are still absent from the actual FastAPI router set: `/api/skills/upload/preview`, `/api/skills/upload`, `/api/github/preview`, `/api/github/install`, `/api/skills/batch/delete`, and `/api/skills/batch/toggle`.
- Direct Marketplace write/admin routes are absent: `POST /api/marketplace/`, `PUT /api/marketplace/{skill_name}`, `PATCH /api/marketplace/{skill_name}/activate`, and `DELETE /api/marketplace/{skill_name}`.
- MCP server lifecycle routes are still absent from `app/routes` and `app/main.py`: `/api/mcp/*` and `/api/admin/mcp/*` are referenced by legacy frontend hooks but not served by the current backend.
- MCP tool governance is partially backed by admin tool policies and run-scoped tool permission decisions, but not by server CRUD, credential lifecycle, department enablement, or a standalone approval inbox.

## Frontend Handling

- Marketplace keeps read, preview, install, and update affordances enabled only when `marketplace:read` and `skill:write` allow them.
- Marketplace direct create/edit/activate/delete affordances stay hidden behind `marketplaceDirectWriteBacked = false`.
- Skills catalog keeps read, toggle, delete, publish-request, and export paths visible when authorized.
- Skills create, edit, ZIP import, GitHub import, and batch actions stay hidden until durable file storage/import/batch routes exist.
- MCP page stays as a governed directory shell with lifecycle and credential controls shown as fail-closed, not as writable controls.

## Backend Follow-Up Needed

Filed backend follow-up: https://github.com/demonsxxxxxx/ai-platform/issues/183

Issue #183 covers:

- Durable user Skill write storage for file create/update/delete.
- Skills ZIP/GitHub import preview and install contracts, or frontend removal of those legacy paths from the product scope.
- Batch toggle/delete contracts, or explicit product decision to remove batch management.
- Marketplace direct publish/edit/admin lifecycle contracts, if those actions are intended beyond publish-request audit.
- MCP server lifecycle, credential governance, department enablement, and approval inbox contracts.
