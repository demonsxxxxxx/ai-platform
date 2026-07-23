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
