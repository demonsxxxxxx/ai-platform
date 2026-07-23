# 211 Release Operations Runbook

This runbook contains host commands, recovery paths, and terminal evidence for
ai-platform releases on the Docker-capable 211 host. Product/source boundaries
live in the guardrails; task ownership, readiness, leases, and break-glass
authority live in `docs/agent-rules/multi-agent-context-workflow.md`.

## Standard Command

Use `tools/release_authority.py deploy-main-commit` with the explicit full commit
fetched from authoritative `origin/main`. This command does not replace the
readiness and ownership gates defined by the multi-agent workflow.

For governed Docker egress, the command resolves the reviewed backend build to
its local immutable `sha256:<64-hex>` Docker image ID and passes that value as
`SANDBOX_EXECUTOR_IMAGE` to API and worker. Do not replace that handoff with a
mutable `ai-platform:<commit>` tag; an operator must rebuild or resolve the
target image ID before retrying when it is unavailable locally.

## Governed Sandbox Overlay Contract

At `<release-root>`, the operator-held environment file must set the exact
release subject and the governed callback boundary without recording a raw key
in terminal evidence:

```text
AI_PLATFORM_SOURCE_COMMIT=<40-lowercase-hex-commit>
SANDBOX_EGRESS_POLICY_ENABLED=true
SANDBOX_CALLBACK_BASE_URL=http://api.sandbox.internal:8020
SANDBOX_EGRESS_PROOF_SIGNING_KEY=<operator-held-current-proof-key>
SANDBOX_EGRESS_PROOF_KEY_ID=<non-secret-current-key-id>
SANDBOX_EGRESS_PROOF_PREVIOUS_KEYS_JSON=<empty-or-bounded-read-only-previous-key-map>
DOCKER_SOCKET_GID=<host-docker-group-id>
```

The current key ID is durable proof metadata, not a secret. Previous keys are
read only for signed `released` or `expired` history; active acquisition and
dispatch require the current key and a fresh proof. Keep the raw values in the
host environment file only.

Use the sandbox overlay explicitly; the placeholders below are contracts, not
real paths, images, commits, or credentials:

```bash
cd <release-root>
sudo -n env \
  AI_PLATFORM_IMAGE=<reviewed-api-worker-image> \
  SANDBOX_EXECUTOR_IMAGE=sha256:<64-lowercase-hex-image-id> \
  docker compose --env-file <release-root>/deploy/ai-platform/.env \
  -f <release-root>/deploy/ai-platform/docker-compose.yml \
  -f <release-root>/deploy/ai-platform/docker-compose.sandbox.yml \
  up -d --no-build api worker workspace-init
```

Before allowing untrusted execution, verify that the API is healthy with the
same runtime commit, that each lease bridge contains only that API witness and
its sandbox, and that the proof key material is present but never printed.

## Readiness Evidence

Before the workflow grants its release lease, the read-only host packet must
identify the publisher and target commits, host and runtime subject, executable
rollback subject, release-authority state and lock holder, Docker/Compose
capability, per-service ownership and recover/adopt compatibility, and the exact
services and method that require mutation. Missing or stale fields block release
work rather than becoming discovery work inside a mutation task.

## Host Command Rules

- Invoke repository Python checks with `python3`; bare `python` is Python 2.7 on
  the host.
- Verifiers that need Docker use `--docker-cmd "sudo -n docker"`.
- `sudo` does not preserve a leading environment assignment. Select a Compose
  image with `sudo -n env AI_PLATFORM_IMAGE=<tag> docker compose ...`, not
  `AI_PLATFORM_IMAGE=<tag> sudo -n docker compose ...`.
- Do not read, copy, export, or quote the real deployment `.env`. Pass it only
  through the target runtime environment and report redacted evidence.

## Offline And Runtime-Only Recovery

- Do not make a smoke check depend on Docker Hub. For sandbox cancel probes,
  prefer an already-local image such as `ai-platform:local` via
  `--cancel-image ai-platform:local`.
- The committed Compose file intentionally does not forward package-index
  variables as build arguments. When dependencies have not changed and package
  download fails, rebuild the local runtime image from the current or backup
  image by copying only `pyproject.toml`, `app/`, `skills/`, and
  `docker-entrypoint.sh`, then recreate with `--no-build`.
- Runtime-only images prepared from a Git archive or Windows snapshot must run
  `chmod +x /app/docker-entrypoint.sh` before container recreation.
- If repeated runtime-only rebases fail with Docker `max depth exceeded`, stop
  stacking layers. Flatten the current healthy container with `docker export`
  and `docker import`, build once from that flat base, and re-run provenance,
  health, and target-path verification.

## Terminal Evidence

A release is complete only after the owner reports the exact commit and image,
container identity and restart counts, API/frontend health, relevant smoke,
rollback subject, authority terminal state, and final source/runtime parity.
Historical evidence or a healthy old runtime does not prove the target release.
