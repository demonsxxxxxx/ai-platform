# AI Platform API

Thin platform service for the enterprise AI Agent platform.

## Responsibilities

- Owns the fixed deployment scope plus workspace, user, agent, skill, session,
  run, file, artifact, and run-event facts. It is not a tenant-management product.
- Stores uploaded files and generated artifacts in MinIO/S3.
- Enqueues AI runs for worker execution.
- Delegates execution through the configured sandbox and Engine adapters.

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
python -m app.schema_migrations apply
python -m app.schema_migrations status
curl http://127.0.0.1:8020/api/ai/ready
```

Compose runs the same migration command as a one-shot dependency before the API
or worker starts. The authenticated `/admin/apply-schema` route remains only as
an emergency-compatible wrapper around the versioned migration runner. See
`docs/architecture/single-enterprise-data-lifecycle.md` for the identity,
schema, retention, and rollback contract.

## Worker

Run one leased job and exit:

```powershell
python -m app.worker_main --once --timeout 1
```

Run the worker loop in compose:

```powershell
docker compose -f deploy/ai-platform/docker-compose.yml --env-file deploy/ai-platform/.env --profile worker up -d --build
```

The worker consumes the platform queue, updates run events/status, and calls the configured executor adapter. The adapter is not the platform source of truth.

## Frontend Compatibility Contract

The deployed frontend entry is operator-configured. The frontend should use
same-origin `/api/*` requests from that entry. The
frontend reverse proxy routes those requests to the platform API. Do not point
the frontend at a non-platform backend or a temporary API proxy.

The platform exposes frontend-compatible `/api/auth/login`, `/api/auth/me`,
`/api/auth/refresh`, `/api/chat/stream`, `/api/sessions/*`, and `/api/upload/*`
routes. The documented login flow is company-account login.

Frontend source lives under `frontend/web` for source ownership and
backend/worker/frontend same-commit review. This does not create a new runtime
entry or a Docker compose release shortcut. See `frontend/web/README.md` and
`docs/README.md` for the governing document hierarchy.

General chat uses the `general-agent` Harness path with `execution_kind=harness_chat`
and no Skill identity. It requires `WORKER_CLAUDE_AGENT_SDK_ENABLED=true` plus
server-side new-api credentials. API containers intentionally do not receive the
SDK execution switch; workers execute both base Harness chat and explicitly
authorized specialized Skill runs. The `general-chat` database row exists only
in upgraded databases that already contain historical v1 Skill runs; clean
installs do not seed it and it is not a selectable Workbench Skill. See
`docs/adr/0005-harness-chat-is-not-a-skill.md`.
