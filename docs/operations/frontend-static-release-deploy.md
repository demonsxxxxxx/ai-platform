# Frontend Formal Runtime Deploy

Status: operational contract for GitHub issue #156. The normal 211 frontend
runtime is the Docker Compose `frontend` service and container
`ai-platform-frontend`, not the historical Python static preview process.

## Runtime Shape

The repo-local runtime target is:

- Compose directory:
  `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform`
- Frontend URL: `http://10.56.0.211:18001/`
- Compose service: `frontend`
- Container: `ai-platform-frontend`
- Image: `${AI_PLATFORM_FRONTEND_IMAGE:-ai-platform-frontend:local}`
- Host/container port: `${AI_PLATFORM_FRONTEND_PORT:-18001}:8080`
- Static server: nginx from `frontend/web/Dockerfile`
- API proxy: `/api/*` to `${AI_PLATFORM_API_UPSTREAM:-http://api:8020}`
- Health endpoint: `/healthz`
- Build provenance endpoint: `/ai-platform-build-provenance.json`

The legacy Python static preview root
`/home/xinlin.jiang/frontend-pr111-smoke` may remain as rollback or comparison
material, but it is not the normal serving path for #156 closure.

## Upstream Pattern Check

Comparable open-source Agent/chat frontends converge on built frontend
artifacts plus a formal runtime:

| Project | Observed frontend deployment pattern | ai-platform takeaway |
| --- | --- | --- |
| LibreChat | The primary Dockerfile builds the client into `client/dist`; its Docker setup can serve static client artifacts behind a formal containerized runtime. | Built static frontend artifacts should be tied to source revision and served by a managed runtime, not an ad-hoc preview process. |
| Open WebUI | The Docker image builds frontend assets and publishes pinned images for production use. | Runtime identity should be image-based and provenance-bound. |
| Dify | Docker Compose runs web, api, worker, nginx, and dependencies as services. | The frontend should be a named Compose service with health/proxy semantics and rollbackable image selection. |

Reference links:

- LibreChat Dockerfile:
  `https://github.com/danny-avila/LibreChat/blob/main/Dockerfile`
- Open WebUI README and Docker discussion:
  `https://github.com/open-webui/open-webui`,
  `https://github.com/open-webui/open-webui/discussions/6228`
- Dify web Dockerfile and Docker Compose docs:
  `https://github.com/langgenius/dify/blob/main/web/Dockerfile`,
  `https://docs.dify.ai/en/self-host/deploy/quick-start/docker-compose`

The ai-platform rule is:

> `http://10.56.0.211:18001/` must be served by the formal
> `ai-platform-frontend` Compose service before #156 can be called
> `211 verified` or `gate closable`.

## Build

Build the frontend image from the same source commit as the API/worker runtime:

```bash
cd /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform
COMMIT_SHA="$(git rev-parse HEAD 2>/dev/null || cat .ai-platform-source-revision)"
COMMIT_SHORT="${COMMIT_SHA:0:7}"
sudo -n docker build \
  --build-arg AI_PLATFORM_BUILD_COMMIT="$COMMIT_SHA" \
  --build-arg AI_PLATFORM_BUILD_DIRTY=false \
  -f frontend/web/Dockerfile \
  -t "ai-platform-frontend:${COMMIT_SHORT}" \
  .
```

If the 211 host cannot pull `node:22-bookworm` or `nginx:1.27-alpine`, build on a
Docker-capable host with registry access, save the image, upload it to the fixed
frontend package directory, and load it on 211:

```bash
sudo -n docker save "ai-platform-frontend:${COMMIT_SHORT}" \
  | gzip > "ai-platform-frontend-${COMMIT_SHORT}.tar.gz"
mkdir -p /home/xinlin.jiang/frontend-pr111-smoke/packages
# Upload ai-platform-frontend-<commit-short>.tar.gz to the packages directory.
gunzip -c "/home/xinlin.jiang/frontend-pr111-smoke/packages/ai-platform-frontend-${COMMIT_SHORT}.tar.gz" \
  | sudo -n docker load
```

Do not upload packages to `/home/xinlin.jiang`. Use
`/home/xinlin.jiang/frontend-pr111-smoke/packages/` for frontend images or
legacy dist packages.

## Deploy

Deploy the formal service through the repo-local Compose file:

```bash
cd /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform
COMMIT_SHA="$(cd ../.. && (git rev-parse HEAD 2>/dev/null || cat .ai-platform-source-revision))"
COMMIT_SHORT="${COMMIT_SHA:0:7}"
sudo -n env \
  AI_PLATFORM_FRONTEND_IMAGE="ai-platform-frontend:${COMMIT_SHORT}" \
  AI_PLATFORM_FRONTEND_PORT=18001 \
  AI_PLATFORM_BUILD_COMMIT="$COMMIT_SHA" \
  AI_PLATFORM_BUILD_DIRTY=false \
  docker compose up -d --no-build frontend
```

Do not pass real `.env` contents through committed docs, logs, or PR comments.
If existing backend services require their runtime `.env`, keep it on 211 and
reference only that it was read by Compose, with values redacted.

## Required Smoke

After deploy, record fresh evidence:

```bash
cd /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform
sudo -n docker compose ps frontend
sudo -n docker inspect ai-platform-frontend \
  --format '{{json .Config.Labels}}'
sudo -n docker inspect ai-platform-frontend \
  --format '{{json .Config.Image}} {{json .Config.Env}}'

curl -fsS -o /tmp/ai-platform-frontend-healthz.txt \
  -w 'healthz_http=%{http_code}\n' http://127.0.0.1:18001/healthz
curl -fsS -o /tmp/ai-platform-root.html \
  -w 'root_http=%{http_code}\n' http://127.0.0.1:18001/
curl -fsS -o /tmp/ai-platform-login.html \
  -w 'login_http=%{http_code}\n' http://127.0.0.1:18001/auth/login
curl -fsS http://127.0.0.1:18001/api/ai/health
curl -fsS http://127.0.0.1:18001/ai-platform-build-provenance.json

(ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null || true) | grep ':18001'
ps -eo pid,ppid,etime,cmd \
  | grep -E 'serve_ai_platform_frontend.py|frontend-pr111-smoke|18001' \
  | grep -v grep || true
```

Acceptance for #156 requires:

- `docker compose ps frontend` shows `ai-platform-frontend` running.
- Port `18001` is owned by Docker/container proxy for `ai-platform-frontend`.
- No standalone Python preview process is required for normal serving.
- `/auth/login` returns the built frontend.
- Logged-in `/chat` reaches the composer.
- `/api/ai/health` returns `{"status":"ok"}` through the frontend runtime.
- `/ai-platform-build-provenance.json` records the deployed commit and
  `"dirty": false`.

## Browser Smoke

Use a company account from a gitignored environment file or process
environment. Evidence must record only variable names and `redacted` values,
never account passwords.

Minimum browser evidence:

- open `http://10.56.0.211:18001/auth/login`;
- log in through the normal flow;
- reach `/chat`;
- verify the composer is visible and usable;
- verify the shell does not redirect back to login.

## Legacy Python Preview

The historical Python static runtime can still be used for emergency comparison:

```bash
python3 /home/xinlin.jiang/frontend-pr111-smoke/tools/serve_ai_platform_frontend.py \
  --host 0.0.0.0 \
  --port 18003 \
  --root /home/xinlin.jiang/frontend-pr111-smoke/dist \
  --api-base http://127.0.0.1:8020
```

Do not run the legacy preview on `18001` while claiming #156 closure.

## Rollback

Prefer image rollback:

```bash
cd /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform
sudo -n env \
  AI_PLATFORM_FRONTEND_IMAGE="ai-platform-frontend:<previous-commit-short>" \
  AI_PLATFORM_FRONTEND_PORT=18001 \
  docker compose up -d --no-build frontend
```

Then repeat the required smoke commands.

If all frontend images are unusable, stop the formal frontend container and use
the legacy Python preview only as an explicitly recorded emergency rollback:

```bash
cd /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform
sudo -n docker compose stop frontend
```

Starting the legacy preview on `18001` reopens #156 because the normal serving
path is no longer the formal frontend service.

## Fixed Upload Directory And Cleanup

Use the fixed package directory:

```bash
mkdir -p /home/xinlin.jiang/frontend-pr111-smoke/packages
find /home/xinlin.jiang -maxdepth 1 -type f \
  \( -name 'ai-platform-frontend-*.tar.gz' -o -name 'ai-platform-frontend-*-dist.tar.gz' \) \
  -mtime +2 -print
find /home/xinlin.jiang -maxdepth 1 -type f \
  \( -name 'ai-platform-frontend-*.tar.gz' -o -name 'ai-platform-frontend-*-dist.tar.gz' \) \
  -mtime +2 -delete
```

Do not delete `frontend-pr111-smoke`, `ai-platform-phaseb`, `new-api`, Docker
volumes, evidence directories, or any path referenced by a live process.

## Status Boundaries

- Passing local traceability or compose tests is `local partial`.
- A PR with passing checks is `PR ready`.
- A merged PR is not automatically `211 verified`.
- `211 verified` requires fresh 211 container, provenance, HTTP, and browser
  evidence from `http://10.56.0.211:18001/`.
- `gate closable` for #156 requires merged code, subagent review evidence,
  211 formal runtime evidence, rollback evidence, and an issue closure comment.
