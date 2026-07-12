# Session Action Contract Closure Phase Status

Status: `local partial`

- [x] Phase 1: revision-68 envelope read back on `codex/session-action-contract-closure` from exact base `935f85506e4c5ceba4c35c5c9b6df2fc9278de17`; `origin/main` equals the base and the starting worktree is clean. The controller supplied scope fingerprint is `sha256:58e0275b5b8e8beac66920f17a7c3ab0e2bf211f6ebc064feadc05f2d509358d` and worktree fingerprint is `sha256:c150d49fa5be6df968b17e14f7a03aed20d61d06fb25cfc63129e34267b53300`. This repository records fingerprints but has no local derivation algorithm; their bound source inputs were rechecked.
- [x] Phase 2: tracking issue [#408](https://github.com/demonsxxxxxx/ai-platform/issues/408) opened without auto-close language.
- [x] Phase 3 RED: backend action tests failed first because `app.session_actions` and its compatibility routes did not exist. The frontend test runner was initially unavailable because this worktree had no `tsx`; after local dependency setup, the intended fail-closed list-url test was active.
- [x] Phase 4 GREEN: `app.session_actions` now centralizes owner/admin same-tenant authorization, title validation, terminal soft delete, and authorized message-prefix fork. LambChat adapters map only public requests and service outcomes. Move/favorite methods, menu affordances, callback plumbing, and unsupported query serialization were removed without adding persistence.
- [x] Phase 5: affected backend tests reported `276 passed`; frontend focused tests reported `16 passed`; `tsc -b`, projection audit, and frontend build exited 0. `python -m compileall -q app tools scripts`, `git diff --check`, exact writable-scope, added-line secret, and unsupported-call scans exited 0.
- [x] Phase 6: initial fixed-head review found no Critical issue and one Important admin-fork users-FK gap. The source schema confirms `sessions.user_id` references `users(id)` while `create_session` does not ensure a user; a RED assertion failed, then the shared service began calling `ensure_user` before fork creation. Fixed-head re-review of `2873cbae..da0c53a5` found no open Critical or Important finding. The review stayed read-only and independently confirmed that denial exits precede `ensure_user`, creation, and message copying.
- [ ] Phase 7: push the reviewed head, open a ready PR without merge/issue closure, post exact-head local verification and review-substitute evidence, and read back required CI.
- [~] Runtime, 211, Docker, B2/readiness, schema/migrations, project/favorite persistence, merge, issue closure, and gate claims remain out of scope.

## Current Contract

`GET /api/sessions` and read projections are backed by the tenant-scoped `sessions`
table. The active frontend nevertheless calls missing `PATCH` and `DELETE`
session routes, an absent message-fork route, and absent move/favorite routes. The
table has canonical owner, tenant, workspace, agent, title, and status fields;
it has no project or favorite authority. No new persistence is permitted.

## Options Considered

1. Add per-route mutation code and leave unsupported frontend controls in place.
   This duplicates authorization and preserves broken affordances. Rejected.
2. Add a shared Session action service for rename/delete/message-fork, with
   LambChat adapters and scoped repository primitives; remove project/favorite
   calls and menu controls because no canonical persistence exists. Recommended.
3. Add project/favorite columns or metadata writes to make all controls work.
   This invents authority and requires a forbidden schema/persistence change.
   Rejected.

## Design And TDD Plan

The service receives an authenticated principal and delegates all persistence to
tenant-scoped repository helpers. It returns `session_not_found` for both absent
and unauthorized resources, so cross-user and cross-tenant callers get no
existence oracle. Owners may mutate their active sessions; AI administrators may
mutate any same-tenant session. A repeated delete for an authorized already-
deleted session returns the same terminal result without another mutation.

Message fork validates the source session and requested message in the same
authorized tenant scope, creates an independent session for the acting user in
the source workspace, and copies the source message prefix through the selected
message. It never crosses tenant or source-user authorization boundaries.

Move and favorite have no backend source of truth. The frontend will remove
their API methods and callbacks, hide the corresponding `SessionMenu` choices,
and fail closed for legacy project/favorite filter requests by issuing no
unsupported query. Existing unrelated project shell code is not redesigned.

### RED Matrix

| Action | Required RED contract |
| --- | --- |
| Rename | owner and same-tenant admin succeed; cross-user, cross-tenant, missing, deleted, blank title, and invalid title deny with no update. |
| Delete | owner and same-tenant admin succeed; cross-user, cross-tenant, and missing deny with no update; authorized repeated delete is terminal and idempotent. |
| Fork | owner and same-tenant admin succeed; cross-user, cross-tenant, missing source, deleted source, missing message, and message from another session deny with no new session/message copy. |
| Route adapters | `PATCH`, `DELETE`, and message-fork map service outcomes to compatible public responses and never implement business rules directly. |
| Fail closed | no frontend request is made for move/favorite, and no visible Session menu affordance remains for either action. |

### Expected Changed Files

- `app/session_actions.py` (new application service)
- `app/models.py`, `app/repositories.py`, `app/routes/lambchat_compat.py`
- `tests/test_lambchat_frontend_compat.py`, `tests/test_repositories.py`
- `frontend/web/src/services/api/session.ts` and its tests
- `frontend/web/src/components/panels/SessionSidebar.tsx`
- `frontend/web/src/components/sidebar/SessionItem.tsx`, `SessionMenu.tsx`,
  `ProjectItem.tsx`, and `sessionFavorites.ts` plus allowed tests
- `frontend/web/src/components/panels/SidebarParts/SessionListContent.tsx`
- `frontend/web/src/hooks/useProjectManager.ts`

No schema, migration, dependency, CI, Docker, deployment, runtime, or 211 file
will be changed.
