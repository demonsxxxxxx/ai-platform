# Docker Packaging Authority

## Phase 1 authority

`pyproject.toml` declares direct Python dependencies and pins the supported lock
tool. `uv.lock` is the reviewed direct and transitive dependency resolution.
Generate and consume it only with `uv 0.12.1`:

```powershell
python -m pip install "uv==0.12.1"
uv lock
uv lock --check
uv sync --locked --extra test --no-install-project
```

The backend Dockerfile uses the same lock with `uv sync --locked`; it must not
derive an unlocked requirements file from `pyproject.toml`. Keep index, proxy,
and credential values out of Git, issue/PR text, and command output. The default
CI and image build use only public package sources.

The backend build and CI run Python 3.13.14. The frontend build and CI run Node
22.23.2 with the `package.json` `pnpm@10.32.1` package-manager contract and the
reviewed `pnpm-lock.yaml`. Focused tests reject version drift among declarations,
Dockerfiles, and workflows.

All external Dockerfile bases use a readable patch tag plus an immutable OCI
index digest:

- `python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8`
- `ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded`
- `node:22.23.2-bookworm@sha256:0557ac14e0d45d02ed563067b82856ca5e7aa3437fa28d98d4350ea9c3d9494a`
- `nginx:1.27.5-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10`

These are multi-platform index subjects verified from the official registries;
Docker resolves a platform manifest at build time. The Phase 1 GitHub jobs
observe Linux amd64 builds only and do not prove every indexed platform. Update a
base by verifying the official tag, index media type, digest, and required
platforms, then update the Dockerfile, version declaration, lock when applicable,
and focused tests in one review.

Pull requests and `main` run the stable `backend required` and `frontend required`
checks. Those checks include real Docker builds. Backend acceptance verifies
imports, HTTP startup, non-root identity, executable entrypoint, source markers,
and existing OCI/release labels. Frontend acceptance verifies the built artifact,
the same commit/label contract, and an HTTP health response from the temporary CI
container. That standalone frontend container overrides its upstream with the
numeric loopback address only for the health probe: the production image keeps
its Compose-network `api:8020` default, while an isolated CI runner has no `api`
DNS subject. On failure, both image jobs report only a bounded log-tail line count,
fixed non-secret startup signals, and container status/exit code; they never dump
container environments or raw log content. The workflows have `contents: read`
only and do not publish, deploy, read deployment configuration, or claim a registry
or 211 runtime subject.

Rollback is an ordinary revert of the reviewed lock, Dockerfile, workflow, and
version declaration changes. It does not retag, publish, deploy, or mutate the
production Compose authority.

## Later phases

Phase 2 owns Registry publication by immutable digest and adds SBOM, provenance
attestation, signing, and scanning before any protected read-only runtime pull.
Phase 3 may add a root local Compose facade and split the existing production
Compose consumers to reviewed `image@sha256` inputs. Only after real layer and
runtime measurements may a later decision split API, worker, or executor images.
Neither phase may rename or duplicate `deploy/ai-platform/docker-compose.yml`, and
both remain separate from 211 release authority and runtime acceptance.
