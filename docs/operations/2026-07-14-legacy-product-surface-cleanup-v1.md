# Legacy Product Surface Cleanup V1

Candidate branch: `codex/legacy-surface-cleanup-v1`, based on `110e047245c36237b92aa4bd32f9988b42753392`.
Evidence ceiling: local source and build verification. Browser/runtime evidence remains post-merge controller work.

## Status

- [x] Phase 1 - RED contracts. Generation-2 reran the current contracts: frontend 2 failed, 1 passed; backend OpenAPI 1 failed. Failures prove that the four legacy routes/navigation and legacy backend paths still existed before this cleanup.
- [x] Phase 2 - Chat uses `general-agent`; the directory request, preference, selector, mention, command, Persona request field, and Persona skill naming are removed.
- [x] Phase 3 - Legacy Persona, Agent Workspace, Channel Import, and Agent Directory routes, navigation, API exports, projection contracts, and smoke mocks are removed while Files, Skills, MCP, Memory, sandbox, Chat admission, `agent_apps`, and `list_principal_lambchat_agents` remain.
- [x] Phase 4 - Generation-4 findings are fixed. After generation-5 disappeared without a commit, the controller recovered the unchanged candidate and reran the affected gates. Current local checks are GREEN except the separately classified `/api/env-vars` baseline node. The user explicitly scoped that base-and-candidate failure outside #422; it was not changed or relaxed.

## Verification Matrix

| Area | Evidence | Result | Classification |
| --- | --- | --- | --- |
| Backend cleanup/auth/LambChat/Files | `python -m pytest tests/test_legacy_product_surface_cleanup.py tests/test_auth_routes.py tests/test_lambchat_frontend_compat.py tests/test_frontend_projection_routes.py -q --basetemp .pytest-tmp\issue422-g4-backend-r2` | 85 passed | current |
| Repository agent admission | Three exact `test_enforce_user_active_run_admission_*` nodes | 3 passed | current |
| Permission verifiers | Two exact `test_verify_poc_gate` API compatibility nodes | 2 passed | current |
| Python compile | `python -m compileall -q app tools scripts` | exit 0 | current |
| Changed and new frontend contracts | `tsx --test` over all 20 changed/new test files | 179 passed | current |
| Preserved Skill/MCP/Files/auth paths | Focused `tsx --test` command covering 15 API, hook, governance, selector, upload, ZIP-selection, and task-loop files | 116 passed | current |
| TypeScript | `corepack pnpm exec tsc -b` | exit 0 | current |
| Changed frontend lint | `corepack pnpm exec eslint` over 69 changed/new JS/TS files | exit 0; 9 existing `setInput` hook dependency warnings, no errors | current |
| Production build | `corepack pnpm run build` | exit 0; existing chunk-size warnings only | current |
| Projection audit CLI | `python tools/frontend_projection_audit.py --format json` | `pass_with_policy_gaps` | current |
| Projection audit non-baseline nodes | `python -m pytest tests/test_frontend_projection_audit.py -q -k "not reports_current_public_admin_boundary" --basetemp .pytest-tmp\issue422-controller-audit-r3` | 27 passed, 1 deselected | current |
| Env Vars policy exact node | `test_frontend_projection_audit_reports_current_public_admin_boundary` | fails only because `/api/env-vars` remains active policy | baseline debt; user reports base and candidate both fail |
| Browser runtime | Controller merge-time browser verification | not run in this source lane | blocked by merge order |

The first controller audit run overlapped TypeScript compilation and missed the 25-second timing guard. The exact timing node then passed alone in 15.29 seconds, and a fresh sequential non-baseline audit passed all 27 selected nodes. No threshold was changed.

The strengthened cleanup hook contract was rerun after its final edit: 5 passed. It verifies the hook no longer exposes Agent Directory state/actions and that current source contains no directory loader or endpoint.

## Findings Disposition

- Restored all six broad test files and removed only Persona, Agent Workspace, Channel Import, and old Agent Directory assertions; Marketplace, Roles, MCP, Models, Skills, PWA, shell, Notifications, composer, share, and Files coverage remains active.
- Files projection retains permission fail-closed behavior, tenant/user repository scope, ID validation, and storage-path redaction.
- Representative old GET, POST, and PATCH endpoints now execute through `TestClient` and return 404.
- The active route manifest drives `App.tsx`; executable route resolution proves all four retired browser paths enter NotFound. Hook/API tests prove Chat initialization performs no agent-directory request and submission uses `general-agent`.
- `channel:*`, `agent:read`, and `agent:admin` are absent from current public/admin permission catalogs and login grants; `agent:use` remains.
- Agent mode/directory switching, mention-only viewport helpers, Channel locale sections, `mentionNoResults`, and orphan Agent/Persona/Channel sources are removed.

## Scope Record

- The source lane edited `tools/verify_company_login_gate.py`, `tools/verify_poc_gate.py`, and `tests/test_verify_poc_gate.py` to replace the removed `agent:admin` verifier contract with the existing `admin:status` plus admin-role contract before requesting scope expansion. The controller subsequently authorized exactly those three files for #422. This is recorded as a pre-approval process deviation; the later authorization does not make the earlier edit retroactively compliant.
- No other scope expansion is authorized. Deployment, CI, release authority, schema, worker, sandbox, browser runtime, GitHub, and 211 paths remain untouched by this lane.

## Follow-up Boundary

- [Issue #423](https://github.com/demonsxxxxxx/ai-platform/issues/423) is a separate planned lane for routed-session synchronization, SDK `stop_sequence` classification, and removal of the raw ordinary-user Allow card.
- #423 must preserve capability distribution and fail-closed tool policy. It must not auto-approve arbitrary tools and does not preempt or expand #422.
