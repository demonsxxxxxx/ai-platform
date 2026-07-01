# Frontend Static Release Deploy

Status: operational contract for the current 211 Python static frontend service.
This document does not convert the frontend to Docker/nginx and does not mark
any deployment as `211 verified`.

## Upstream Pattern Check

Comparable open-source Agent/chat frontends mostly converge on the same
deployment shape:

| Project | Observed frontend deployment pattern | ai-platform takeaway |
| --- | --- | --- |
| LibreChat | The primary Dockerfile builds the client into `client/dist`, exposes the Node API, and documents an optional nginx client stage that copies `client/dist` into `/usr/share/nginx/html`. | Static frontend artifacts are release units; nginx is a valid future packaging target, but not required for the current 211 Python static entry. |
| Open WebUI | The Dockerfile uses a Node build stage for the WebUI frontend, then a Python backend runtime stage; public docs also recommend pinned Docker image tags for production instead of floating tags. | Build output and runtime image should carry version identity; production should avoid unpinned, mutable frontend state. |
| Dify | The web README supports `pnpm -C web run build` plus `pnpm -C web run start`, and Docker builds from the repository root with `web/Dockerfile`; compose deployment runs `web`, `api`, `worker`, nginx, and dependencies as separate services. | A mature target is multi-service packaging, but a static release root can still enforce fixed artifact identity before that migration. |

Reference links:

- LibreChat Dockerfile:
  `https://github.com/danny-avila/LibreChat/blob/main/Dockerfile`
- Open WebUI README and Docker discussion:
  `https://github.com/open-webui/open-webui`,
  `https://github.com/open-webui/open-webui/discussions/6228`
- Dify web Dockerfile and Docker Compose docs:
  `https://github.com/langgenius/dify/blob/main/web/Dockerfile`,
  `https://docs.dify.ai/en/self-host/deploy/quick-start/docker-compose`

The near-term ai-platform rule is therefore:

> Keep 211 on the existing Python static service, but publish frontend artifacts
> into immutable release directories and activate them through a stable pointer.

## Current 211 Runtime Boundary

The active 211 frontend entry remains:

- URL: `http://10.56.0.211:18001/`
- Runtime kind: Python static service
- Runtime root: `/home/xinlin.jiang/frontend-pr111-smoke`
- Active dist compatibility path:
  `/home/xinlin.jiang/frontend-pr111-smoke/dist`

The release layout under the runtime root is:

```text
/home/xinlin.jiang/frontend-pr111-smoke/
  dist -> releases/<commit>/dist
  current -> releases/<commit>/dist
  releases/
    <commit>/
      dist/
        index.html
        ai-platform-build-provenance.json
        assets/
  backups/
    dist-backup-before-<commit-short>-<timestamp>/
  packages/
    ai-platform-frontend-<commit-short>-dist.tar.gz
```

Only `dist` and `current` are activation pointers. Do not serve directly from
`packages/` or from the server home directory.

## Package Creation

Build from a clean source commit:

```bash
cd frontend/web
corepack pnpm install --frozen-lockfile
corepack pnpm run ci:verify
cd ../..
tar -czf ai-platform-frontend-<commit-short>-dist.tar.gz -C frontend/web dist
```

Before upload, confirm the package contains:

- `dist/index.html`
- `dist/ai-platform-build-provenance.json`
- `dist/assets/*`

The provenance file must contain the target commit and `"dirty": false`.

## Fixed Upload Directory

Upload packages to the fixed package directory:

```bash
ssh s211 'mkdir -p /home/xinlin.jiang/frontend-pr111-smoke/packages'
scp ai-platform-frontend-<commit-short>-dist.tar.gz \
  s211:/home/xinlin.jiang/frontend-pr111-smoke/packages/
```

Do not upload new frontend packages to `/home/xinlin.jiang`.

For old ad-hoc packages already in the server home directory, delete only
inactive package files older than two days after confirming they are not the
active runtime root:

```bash
ssh s211 'find /home/xinlin.jiang -maxdepth 1 -type f -name "ai-platform-frontend-*-dist.tar.gz" -mtime +2 -print'
ssh s211 'find /home/xinlin.jiang -maxdepth 1 -type f -name "ai-platform-frontend-*-dist.tar.gz" -mtime +2 -delete'
```

Do not delete `frontend-pr111-smoke`, `ai-platform-phaseb`, `new-api`, Docker
volumes, evidence directories, or any path that a live process references.

## Activation

Run the deploy helper on the host that owns the static runtime root:

```bash
cd /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform
python3 tools/deploy_frontend_static.py \
  --package-path /home/xinlin.jiang/frontend-pr111-smoke/packages/ai-platform-frontend-<commit-short>-dist.tar.gz \
  --frontend-root /home/xinlin.jiang/frontend-pr111-smoke \
  --expected-commit <commit-sha> \
  --api-base http://127.0.0.1:8020 \
  --format json
```

The helper refuses to activate a package when:

- `dist/ai-platform-build-provenance.json` is missing or invalid;
- the build commit does not match `--expected-commit`;
- the build provenance says the source tree was dirty;
- the tarball contains absolute paths, parent-directory traversal, links, or
  non-file/non-directory entries.

After activation, restart the current Python static service with the existing
service command only when the active process did not pick up the pointer change.
Keep restart as an explicit operator action; do not hide it in package upload.

## Smoke Checks

Run these after activation or restart:

```bash
curl -fsS -o /tmp/ai-platform-root.html -w 'root_http=%{http_code}\n' http://127.0.0.1:18001/
curl -fsS -o /tmp/ai-platform-login.html -w 'login_http=%{http_code}\n' http://127.0.0.1:18001/auth/login
curl -fsS http://127.0.0.1:18001/api/ai/health
cat /home/xinlin.jiang/frontend-pr111-smoke/dist/ai-platform-build-provenance.json
```

The active provenance commit must equal the deployed commit. Browser smoke and
screenshots remain separate evidence before claiming `211 verified`.

## Rollback

Rollback is pointer-only when the target release already exists:

```bash
cd /home/xinlin.jiang/frontend-pr111-smoke
ln -sfn releases/<previous-commit>/dist current
ln -sfn releases/<previous-commit>/dist dist
```

Then restart the Python static service only if needed and repeat the smoke
checks above.

## Status Boundaries

- Passing `tools/deploy_frontend_static.py` locally is `local partial`.
- A PR with passing CI can be `PR ready`.
- A merged PR is not automatically `211 verified`.
- `211 verified` requires fresh runtime provenance, HTTP smoke, and browser
  evidence from `http://10.56.0.211:18001/`.
- `gate closable` requires merged code, deployment evidence, issue evidence,
  and no open acceptance blocker for the relevant gate.
