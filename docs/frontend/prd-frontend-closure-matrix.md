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

PR #264 is the latest merged frontend evidence slice for the governed PRD
context-panel and smoke-state surface.

| Field | Evidence |
| --- | --- |
| PR | PR #264 |
| Status | merged-main 211 verified |
| Merge commit | `94f0b20fcf441fdcbde730a1edafb2c1dbdcbf59` |
| 211 frontend entry | `http://10.56.0.211:18001/` |
| 211 frontend provenance | commit `94f0b20fcf441fdcbde730a1edafb2c1dbdcbf59`, `dirty=false` |
| HTTP smoke | `/`, `/auth/login`, `/chat`, `/skills`, and `/marketplace` returned 200; backend `/api/ai/health` returned ok |
| Browser smoke | company-account browser login reached `/chat`; governed routes hydrated without login redirects; evidence credentials are redacted |
| Governance states | ready for apps, marketplace, roles, MCP, persona, files, channels, and settings; loading for skills; forbidden for `/shared/smoke-denied` |
| Composer evidence | `/` command menu, `$` Skills selector/chip, MCP selector visibility, and file upload affordance |
| Release tarball | `/home/xinlin.jiang/frontend-releases/20260628-94f0b20/ai-platform-frontend-94f0b20-dist.tar.gz` |
| Release tarball SHA256 | `e71185e112f7fc92b89fba262e9f1ba5bdc0c170c2357c7f2d28c8af0122134b` |
| Backup before switch | `/home/xinlin.jiang/frontend-pr111-smoke/dist-backup-before-94f0b20-20260628-182749` |
| Browser evidence JSON | `/home/xinlin.jiang/frontend-releases/evidence/pr264-94f0b20-211-browser-smoke/211-smoke-94f0b20.json` |
| Browser evidence bundle | `/home/xinlin.jiang/frontend-releases/evidence/pr264-94f0b20-211-browser-smoke-evidence.tar.gz` |
| Browser evidence bundle SHA256 | `158353a2ed6879c5fd7a062c445e1ca23f227cf8febe61064b89b37def6f050d` |

Formal GitHub review metadata is still absent for PR #264. The PR has a posted
main-agent substitute review note, but GitHub `reviewDecision` remains empty.
That means the current status is not formal `reviewed`. The evidence supports
`merged` and `211 verified` for the PR #264 frontend slice, not a full-program
`gate closable` claim for #81.

Credentials are read only from gitignored environment files or process
environment variables such as `AI_PLATFORM_LOGIN_USERNAME` and
`AI_PLATFORM_LOGIN_PASSWORD`. Evidence and comments must record only the source
variable names and `redacted` placeholders, never credential values.

## Phase Matrix

| Scope | Closure state | Current authority |
| --- | --- | --- |
| Phase 1A foundation | Accepted as historical frontend foundation evidence. | Existing auth, session, RBAC, projection audit, build, packaged/static entry, and 211 entry evidence. |
| Phase 1B shell and composer parity | Frontend evidence is present through the merged PR chain ending with PR #264. | LibreChat-style shell, slash command menu, `$ Skills selector`, selected Skill chip, MCP selector evidence, file upload affordance, route hydration, and forbidden shared route are covered by the committed browser-smoke helper and 211 evidence. |
| Phase 1C governance and collaboration surface parity | Frontend evidence is present where current backend projections exist; missing backend write products remain fail-closed or read-only. | `/apps`, `/skills`, `/marketplace`, `/roles`, `/mcp`, `/persona`, `/files`, `/channels`, `/settings`, and `/shared/smoke-denied` are covered by company-account route smoke, governance states, and the authenticated right context panel. |
| Phase 2 backend-backed expansion | Not a frontend-only closure item. | Requires backend contracts and runtime evidence before any real product closure claim. |

## PRD Acceptance Mapping

| Requirement | Evidence status |
| --- | --- |
| Projection audit | Covered by `pnpm run ci:verify` and the committed projection-audit-first frontend workflow. |
| Secret-safe browser evidence | Covered by `frontend/web/scripts/prd-closure-browser-smoke.mjs` and `pnpm run smoke:prd-closure`. |
| Local compile and hygiene | Closure PRs must run `python -m compileall -q app tools scripts` and `git diff --check`. |
| Frontend verification | Closure PRs must run `pnpm run ci:verify`; focused source tests can narrow the local loop before the full check. |
| Company-account browser login | Covered by the PR #264 211 browser smoke, which reached `/chat` with credentials redacted. |
| ordinary workflow | Smoke evidence covers login, `/chat`, slash command menu, `$ Skills selector`, selected Skill chip, MCP selector evidence, file upload affordance, route hydration, and no post-login redirects. |
| admin workflow | Smoke evidence covers `/roles`, `/mcp`, `/channels`, `/settings`, and governance state exposure inside the authenticated shell. |
| Frontend governance states | Smoke covers ready workbench routes and the forbidden shared route `/shared/smoke-denied`. |
| Route coverage | Smoke covers `/chat`, `/apps`, `/skills`, `/marketplace`, `/roles`, `/mcp`, `/persona`, `/files`, `/channels`, `/settings`, and `/shared/smoke-denied`. |
| Runtime identity | 211 static provenance for the active frontend matches the merge commit used in evidence. |
| Right context panel | PR #264 projects run state, selected Skills, MCP tools, file attachments, and pending permission request counts from governed client state. |

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
#81 frontend Phase 1 evidence is strong and current through PR #264, but #81 is
not a full-program `gate closable` issue until Phase 2 backend-backed expansion
has its own accepted evidence and maintainer closure decision.
```
