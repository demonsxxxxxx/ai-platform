# Frontend Complete Backend Contract Summary

Date: 2026-06-23
Branch: `codex/frontend-complete-pass-20260623`
Status: `PR ready` frontend evidence after backend #183 closure; not `reviewed`, not `merged`, not `gate closable`

## Confirmed Backed Contracts

- PR #177 is merged and backs public Skills/Marketplace read and install/update contracts.
- PR #187, #189, #190, #191, #192, #193, #194, #195, and #196 are merged and close #183's backend contract backlog.
- Public Skills are backed under `/api/skills/`: list, detail, file read, tenant availability toggle/delete, publish-request audit, durable user file overlays, ZIP import overlays, and GitHub import paths.
- Marketplace contracts are backed under `/api/marketplace/`: list, tags, detail, files, install/update, and direct lifecycle/admin operations where authorized.
- MCP contracts are backed under `/api/mcp/*` and `/api/admin/mcp/*` for read projection, server CRUD/toggle metadata, credential fingerprint/metadata redaction, department filtering, and registry-first projection.
- Tool permission request/decision contracts are backed under `/api/ai/runs/{run_id}/tool-permissions/...`; the standalone approval inbox is backed under `/api/ai/tool-permissions/inbox`.
- Public model catalog reads are backed under `/api/agent/models/available`; the frontend uses this as the read-only model catalog source.

## 211 Runtime Notes

- #183 has current-main consolidated 211 backend smoke evidence recorded on the issue and is closed.
- The final #183 backend runtime evidence reports merged main `0a9e70a41f2e86afce2be2294b21d2f5651d448d` running on 211 with `ai-platform:0a9e70a-issue183-approval-inbox-runtime-only-v1`.
- That consolidated smoke covered durable Skill overlays, ZIP/GitHub import paths, Marketplace lifecycle, MCP lifecycle/credential/department filtering, standalone approval inbox, schema objects, redaction, cleanup, and log scan.
- The current frontend PR still requires its own exact-head 211 static frontend provenance and route smoke before claiming `211 verified` for the latest pushed head.

## Remaining Frontend Boundary

- This PR is a frontend convergence pass. It does not by itself close the broader Phase 1/Phase 2 frontend absorption issue #82.
- `/roles` is login-reachable and visually converged, but it still consumes the compatibility `/api/roles` endpoint from `app/routes/lambchat_compat.py`.
- On 211, `GET /api/roles` currently returns an empty compatibility projection (`{"roles":[],"total":0,...}`), not a durable ai-platform RBAC/admin projection.
- The projection audit keeps `/api/roles` as `ordinary_user_reachable_legacy_routes_need_policy_enforcement_or_ai_platform_remap`; this should remain tracked under #82 unless split into a narrower backend/admin projection issue.
- The role write controls remain gated by `role:manage`; the compatibility endpoint does not make role CRUD gate-closable.

## Frontend Handling

- Marketplace keeps read, preview, install, update, and lifecycle affordances governed by backend permissions and explicit fail-closed state handling.
- Skills catalog keeps read, file overlay, import, batch, publish-request, and export paths visible only when authorized and backed.
- MCP renders a governed directory/lifecycle shell using the backend registry projection and keeps unsupported or permission-denied controls fail-closed.
- Models uses `/api/agent/models/available` and derives provider summaries from returned model data instead of depending on the absent provider-list endpoint.
- Role management remains a read-visible, write-gated surface pending the #82 RBAC replacement boundary described above.
