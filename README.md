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

## Deployment quick start

Both managed environments use the same repository-owned entry point. The
required profile selects the environment-specific safety controller, while
physical host assignment remains in the deployment inventory.

### Internal test

After the one-time host configuration is in place, deploy the newest qualified
immutable Deployment Release with one command:

```bash
./scripts/deploy-latest.sh --profile internal-test --latest
```

The command anonymously resolves the repository's latest immutable
`deployment-<commit>-<run>-<attempt>` Release, verifies its GitHub SHA-256 and
strict image manifest, materializes the exact qualified commit, pulls the exact
Backend and Frontend GHCR digests, starts the existing Compose project, and runs
API, container, and OpenSandbox health checks. Startup or health failure makes
one image rollback attempt while preserving the existing data volumes.

Repository source, Release metadata, and the small manifest asset are public.
The quickstart removes inherited `GH_TOKEN` and `GITHUB_TOKEN`, sends no
Authorization header, and does not replay Actions, SBOM, Trivy, signature, or
provenance checks on the host. Packaging retains that complete evidence as its
30-day Actions artifact and publishes only after GitHub immutable releases are
enabled. The Docker host must already be logged in to `ghcr.io`. Existing
deployments reuse
the managed `.env` path from `incoming/latest-main.json`; the first deployment
supplies it once:

```bash
./scripts/deploy-latest.sh --profile internal-test --latest \
  --env-file /data/ai-platform-internal-test/config/stable/.env
```

The env file must be an owner-matching `0600` regular file under the managed
`config` directory. The quickstart reads only its path and metadata. See
`docs/operations/release-operations-runbook.md` for host preparation, failure
semantics, and runtime acceptance boundaries.

### Production rebuild or update

The production profile uses the governed OpenSandbox overlay. On a rebuilt host
with Python 3.11+, Docker Compose v2, systemd, and Docker `runsc`, first restore
these files from the approved secret store:

- `/data/ai-platform-prod/config/production/.env` — `root:root 0600`
- `/etc/ai-platform/opensandbox/server.env` — `root:root 0600`
- `/etc/ai-platform/opensandbox/server.toml` —
  `root:<OPENSANDBOX_SERVER_GID> 0640`

The initial OpenSandbox file shapes are documented in
`deploy/opensandbox/server-production.env.example` and
`deploy/opensandbox/server-production.toml.example`; real values never belong
in Git.

The application env must use the same lifecycle URL and API key as those host
files. Its OpenSandbox executor is the release-authority backend workload image,
not the host service's `runtime.execd_image`. Pin the OpenSandbox server, execd,
and egress sidecar by digest; the server digest must have been verified against
an approved `server/v0.1.13` or newer release. The production TOML binds the
trusted Server container to the private lifecycle address, selects the fixed
internal sandbox network, denies host bind mounts, and retains `dns+nft` only as
the pinned upstream egress-sidecar configuration; application requests omit the
incompatible SDK `networkPolicy`.

Docker with Compose v2, systemd, the Docker `runsc` runtime, and the exact
active `ai-platform-opensandbox-network-guard.service` from the target checkout
must already be installed. The root Docker account must be logged in to GHCR;
GitHub source and immutable Deployment Release metadata are read anonymously.

Then rebuild the OpenSandbox host service and production application from the
newest qualified Deployment Release:

```bash
sudo -n ./scripts/deploy-latest.sh --profile production --latest \
  --env-file /data/ai-platform-prod/config/production/.env
```

Later production updates reuse the approved environment path:

```bash
sudo -n ./scripts/deploy-latest.sh --profile production --latest
```

Routine application updates require the admitted OpenSandbox host configuration
to remain unchanged. A Server/API-key/execd/egress or host-network change is a
separate planned host maintenance operation, not an implicit image-update side
effect.

The command refuses partial or legacy production contours, requires quiescence
before updating an existing direct-OpenSandbox runtime, and restores the
previous verified images if target startup or parity fails. A successful command
proves deployment smoke and exact runtime parity; the real application-owned
OpenSandbox create/execute/file/cleanup acceptance remains a separate production
gate. The OpenSandbox server is a trusted host control-plane component with
effective Docker daemon authority through its socket; see
`docs/operations/production-bootstrap.md` and the release runbook for that
boundary and the host prerequisites.

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
