# AI Platform API

Thin platform service for the enterprise AI Agent platform.

## Responsibilities

- Owns tenant, workspace, agent, skill, session, run, file, artifact, and run event facts.
- Stores uploaded files and generated artifacts in MinIO/S3.
- Enqueues AI runs for worker execution.
- Delegates execution to adapters such as the existing 211 review runtime.

## Local compose

```powershell
Copy-Item deploy/ai-platform/.env.example deploy/ai-platform/.env
docker compose -f deploy/ai-platform/docker-compose.yml --env-file deploy/ai-platform/.env up -d --build
```

## Health check

```powershell
curl http://127.0.0.1:8020/api/ai/health
```

## Company Login

The frontend shell should call the platform login endpoint and let the platform
validate credentials through the existing account service. Use real credentials
only in local curl/runtime input; do not commit them.

```powershell
curl -i -X POST http://127.0.0.1:8020/api/ai/auth/login `
  -H "Content-Type: application/json" `
  -d "{\"user_name\":\"<work-id>\",\"password\":\"<password>\"}"
curl -b "ai_platform_session=<cookie>" http://127.0.0.1:8020/api/ai/auth/me
```

## Smoke test

```powershell
curl -X POST http://127.0.0.1:8020/api/ai/admin/apply-schema
curl http://127.0.0.1:8020/api/ai/health
```

## Worker

Run one leased job and exit:

```powershell
python -m app.worker_main --once --timeout 1
```

Run the worker loop in compose:

```powershell
docker compose -f deploy/ai-platform/docker-compose.yml --env-file deploy/ai-platform/.env --profile worker up -d --build
```

The worker consumes the platform queue, updates run events/status, and calls executor adapters. The 211 runtime remains an executor adapter; it is not the platform source of truth.

## Frontend Compatibility Contract

The deployed frontend entry is:

```text
http://10.56.0.211:18001/
```

The frontend should use same-origin `/api/*` requests from that entry. The
frontend reverse proxy routes those requests to the platform API. Do not point
the frontend at a non-platform backend or a temporary API proxy.

The platform exposes frontend-compatible `/api/auth/login`, `/api/auth/me`,
`/api/auth/refresh`, `/api/chat/stream`, `/api/sessions/*`, and `/api/upload/*`
routes. The documented login flow is company-account login.

Frontend source now lives under `frontend/web` for source ownership and
backend/worker/frontend same-commit review. This is a source migration step,
not a new frontend runtime entry and not a Docker compose one-command startup
gate. See `frontend/web/README.md` and
`docs/frontend/ai-platform-frontend-migration.md`.

General chat uses the `general-agent` / `general-chat` seed and requires
`CLAUDE_AGENT_SDK_ENABLED=true` plus server-side new-api credentials. Word
review/translation still use the controlled migration delegate for artifact
generation while the SDK skill files are being ported into the worker image.
