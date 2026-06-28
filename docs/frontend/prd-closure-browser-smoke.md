# Frontend PRD Closure Browser Smoke

This helper records browser-level evidence for the post-login frontend PRD
acceptance path without storing credentials in source, logs, comments, or
committed evidence.

## Scope

The smoke helper lives at:

```text
frontend/web/scripts/prd-closure-browser-smoke.mjs
```

It checks the official post-login workbench entry by default:

```text
http://10.56.0.211:18001
```

It collects redacted JSON evidence for:

- company-account login reaching the authenticated shell;
- frontend build provenance and optional expected commit match;
- post-login routes for chat, apps, skills, marketplace, roles, MCP, persona,
  files, channels, settings, and a denied shared route;
- slash command menu entries;
- `$` Skills selector rows and selected Skill chip when available;
- MCP selector rows and selected MCP chip or denied/unavailable state;
- file reference selectors or explicit fail-closed unavailable evidence;
- frontend governance states for ready, degraded, and forbidden routes.

This helper does not close issue #81 by itself. Treat a local helper run as
`local partial` until a PR exists, review/substitute review is posted, and 211
runtime evidence is recorded against the claimed commit.

## Credentials

Credentials must be supplied through environment variables or a gitignored
`.env` file. Do not paste credential values into commands, PR bodies, issue
comments, docs, or evidence output.

Accepted variable names:

```text
AI_PLATFORM_LOGIN_USERNAME
AI_PLATFORM_LOGIN_PASSWORD
AI_PLATFORM_SMOKE_USERNAME
AI_PLATFORM_SMOKE_PASSWORD
AI_PLATFORM_TEST_USERNAME
AI_PLATFORM_TEST_PASSWORD
AI_PLATFORM_FRONTEND_LOGIN_USERNAME
AI_PLATFORM_FRONTEND_LOGIN_PASSWORD
```

The repository root `.env` and `frontend/web/.env` are gitignored. The helper
records only the source variable name, plus `redacted` placeholders.

## Local Command

From `frontend/web`:

```powershell
pnpm run test:prd-closure-smoke-source
pnpm run smoke:prd-closure -- --env-file ..\..\.env --output ..\..\.codex-tmp\frontend-prd-smoke.json --screenshot-dir ..\..\.codex-tmp\frontend-prd-smoke-screens
```

When the browser executable is not discoverable, pass one of:

```powershell
pnpm run smoke:prd-closure -- --chrome-path "C:\Program Files\Google\Chrome\Application\chrome.exe" --env-file ..\..\.env
pnpm run smoke:prd-closure -- --cdp-url http://127.0.0.1:9222 --env-file ..\..\.env
```

To pin evidence to a specific deployed frontend build:

```powershell
pnpm run smoke:prd-closure -- --expected-commit <commit_sha> --env-file ..\..\.env --output ..\..\.codex-tmp\frontend-prd-smoke.json
```

The JSON output and screenshots are generated evidence drafts and must stay out
of git unless they are separately reviewed, redacted, and promoted through the
repository release-evidence process.

## Status Boundary

- `local partial`: helper source tests or local smoke ran, but no PR/runtime
  evidence closure exists.
- `PR ready`: focused tests and relevant build checks pass, and the PR body
  documents how to run the smoke without secrets.
- `reviewed`: formal review or accepted substitute review is present.
- `211 verified`: deployed 211 provenance matches the claimed commit and HTTP
  plus browser smoke pass.
- `gate closable`: parent issue closure evidence exists after merge, review,
  211 verification, ordinary/admin workflow acceptance, and maintainer closure
  decision.
