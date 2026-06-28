# Frontend PRD Closure Matrix

This matrix is the source-of-truth handoff for completing the frontend side of
the LibreChat-style UI absorption PRD without spreading the remaining frontend
evidence across more unrelated pull requests.

## Single active closure PR

The remaining frontend closure work must be carried by one active closure pull
request with non-closing wording:

```text
Refs #81
```

The PR body must not use `Closes #81` or any equivalent auto-close wording
until the issue has a maintainer closure decision. The parent issue #81 tracks
the broader frontend UI absorption program, so a frontend closure PR may record
frontend acceptance evidence but must not convert missing backend product
contracts into a full-program `gate closable` claim.

Status boundary: this is not a full-program `gate closable` claim.

## Latest Runtime Evidence

PR #267 is the current active frontend closure PR for the governed PRD shell
and smoke-state surface. Its current-head 211 evidence must be read from the PR
evidence comment and live 211 provenance because any commit that edits this
matrix necessarily changes the head SHA after the file is written. While PR
#267 is still open and lacks formal GitHub review metadata, it is not
`reviewed`, not `merged`, and not `gate closable`.

| Field | Evidence |
| --- | --- |
| PR | PR #267 |
| Status boundary | `PR ready` after checks; `211 verified` only when live provenance and the latest PR evidence comment match the current PR head; not `reviewed`, not `merged`, not `gate closable` while open |
| PR state | open, ready for review, mergeable, GitHub `reviewDecision` empty at the latest check |
| GitHub checks | `projection audit, lint, build, trace` success; `packaged image build` success |
| 211 frontend entry | `http://10.56.0.211:18001/` |
| 211 frontend service | Python static service rooted at `/home/xinlin.jiang/frontend-pr111-smoke/dist`, API base `http://127.0.0.1:8020` |
| 211 frontend provenance | Must be checked live against the current PR head before claiming `211 verified` |
| HTTP smoke | `/` returned 200; `/auth/login` returned 200; backend `/api/ai/health` returned ok |
| Browser smoke | company-account browser login reached `/chat`; governed routes hydrated without login redirects; evidence credentials are redacted |
| Governance states | ready for apps, skills, marketplace, roles, MCP, persona, files, channels, and settings; forbidden for `/shared/smoke-denied` |
| Composer evidence | `/` command menu, `$` Skills selector/chip, MCP selector visibility, and file upload affordance |
| Backup before switch | Recorded in the latest PR #267 evidence comment for the deployed head |
| PR evidence comment | Latest PR #267 211 deploy evidence comment; it must use `Refs #81` only |

Formal GitHub review metadata is still absent for PR #267. The review bot
comment on PR #267 reports a Codex usage-limit blocker instead of a review.
The evidence supports `PR ready`, and supports `211 verified` only for the
current head named by live provenance and the latest PR evidence comment. It
does not support `reviewed`, `merged`, or a full-program `gate closable` claim
for #81.

Prior merged evidence remains PR #264 at merge commit
`94f0b20fcf441fdcbde730a1edafb2c1dbdcbf59`. That slice is merged historical
evidence for the governed PRD context-panel and smoke-state surface, but it is
not the active 211 runtime after the PR #267 deployment.

Credentials are read only from gitignored environment files or process
environment variables such as `AI_PLATFORM_LOGIN_USERNAME` and
`AI_PLATFORM_LOGIN_PASSWORD`. Evidence and comments must record only the source
variable names and `redacted` placeholders, never credential values.

## Phase Matrix

| Scope | Closure state | Current authority |
| --- | --- | --- |
| Phase 1A foundation | Accepted as historical frontend foundation evidence. | Existing auth, session, RBAC, projection audit, build, packaged/static entry, and 211 entry evidence. |
| Phase 1B shell and composer parity | Frontend evidence is present through the active PR #267 closure slice and the prior merged chain ending with PR #264. | LibreChat-style shell, slash command menu, `$ Skills selector`, selected Skill chip, MCP selector evidence, file upload affordance, route hydration, and forbidden shared route are covered by the committed browser-smoke helper and 211 evidence. |
| Phase 1C governance and collaboration surface parity | Frontend evidence is present where current backend projections exist; missing backend write products remain fail-closed or read-only. | `/apps`, `/skills`, `/marketplace`, `/roles`, `/mcp`, `/persona`, `/files`, `/channels`, `/settings`, and `/shared/smoke-denied` are covered by company-account route smoke, governance states, and the authenticated right context panel. Source-level closure tests also cover share ACL unavailable/denied/revoked/expired states, governed channel import unavailable state, read-only Skills/Marketplace catalog shells, fail-closed group availability toggles, and MCP lifecycle governance without raw server controls. |
| Phase 2 backend-backed expansion | Not a frontend-only closure item. | Requires backend contracts and runtime evidence before any real product closure claim. |

## PRD Acceptance Mapping

| Requirement | Evidence status |
| --- | --- |
| Projection audit | Covered by `pnpm run ci:verify` and the committed projection-audit-first frontend workflow. |
| Secret-safe browser evidence | Covered by `frontend/web/scripts/prd-closure-browser-smoke.mjs` and `pnpm run smoke:prd-closure`. |
| Local compile and hygiene | Closure PRs must run `python -m compileall -q app tools scripts` and `git diff --check`. |
| Frontend verification | Closure PRs must run `pnpm run ci:verify`; focused source tests can narrow the local loop before the full check. |
| Company-account browser login | Covered by the PR #267 211 browser smoke, which reached `/chat` with credentials redacted. |
| ordinary workflow | Smoke evidence covers login, `/chat`, slash command menu, `$ Skills selector`, selected Skill chip, MCP selector evidence, file upload affordance, route hydration, and no post-login redirects. |
| admin workflow | Smoke evidence covers `/roles`, `/mcp`, `/channels`, `/settings`, and governance state exposure inside the authenticated shell. |
| Frontend governance states | Smoke covers ready workbench routes and the forbidden shared route `/shared/smoke-denied`. |
| Route coverage | Smoke covers `/chat`, `/apps`, `/skills`, `/marketplace`, `/roles`, `/mcp`, `/persona`, `/files`, `/channels`, `/settings`, and `/shared/smoke-denied`. |
| Runtime identity | 211 static provenance for the active frontend must match the PR #267 head commit named in the latest PR evidence comment before `211 verified` can be claimed. |
| Right context panel | PR #267 projects run state, selected Skills, MCP tools, file attachments, and pending permission request counts from governed client state. |
| Phase 1C share and channel surfaces | `shareChannelFailClosedSource.test.ts` covers `/shared/:shareId` fail-closed unavailable states and `/channels` governed channel import without fake import success. |
| Phase 1C marketplace/group toggle surfaces | `governancePhase1Closure.test.ts` covers read-only Skills/Marketplace catalog shells, permission-gated marketplace writes, fail-closed group availability toggle UI, and MCP lifecycle governance without raw controls. |
| Phase 1C active route contract | `frontendPhase1ClosureContract.test.ts` covers active routes for `/apps`, `/skills`, `/marketplace`, `/mcp`, `/channels`, and `/shared/:shareId` plus the fail-closed write surfaces used by Phase 1C. |

## Phase 2 Boundary

The following items remain outside frontend-only closure and must be tracked as
backend-backed work before #81 can be closed as the full program:

Phase 2 backend-backed expansion is not a frontend-only closure item.

- department/group Skill marketplace policy writes;
- MCP lifecycle and policy assignment;
- session-share ACL creation and lifecycle;
- channel import write/import expansion beyond current governed projections;
- users/roles/departments, model admin, settings, and notifications as real
  backed product surfaces.

These gaps must be represented as read-only, unavailable, forbidden, degraded,
or otherwise fail-closed UI until backend contracts and runtime smoke exist.
They are not a reason to keep creating new frontend polish PRs, and they are
not something a frontend-only PR can honestly close.

## Status Language

Use these labels exactly:

- `PR ready`: code, docs, and local focused verification are ready for review.
- `reviewed`: a formal review or accepted substitute review is posted.
- `merged`: the PR is merged.
- `211 verified`: 211 provenance and browser or route smoke match the claimed
  commit.
- `gate closable`: review, merge, local evidence, docs, 211 evidence, and issue
  closure evidence are all present.

For this matrix, the valid parent status is:

```text
#81 frontend Phase 1 evidence is strong and current through PR #267, but #267
is still open and not formally reviewed. #81 is not a full-program
`gate closable` issue until the active PR is reviewed and merged, 211 evidence
is accepted, Phase 2 backend-backed expansion has its own accepted evidence, and
a maintainer makes the closure decision.
```
