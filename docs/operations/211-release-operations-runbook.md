# 211 Release Operations Runbook

This runbook contains operational recovery details for ai-platform releases on
the Docker-capable 211 host. `AGENTS.md` and the guardrails remain authoritative
for source, secret, ownership, and verification boundaries.

## Release Ownership And Readiness

- Use one project-bound persistent release owner and one mutation lease.
- Record `RELEASE_READINESS_PASS` for the exact publisher, target commit,
  runtime, host, rollback subject, authority state, Docker/Compose capability,
  and current per-service ownership before granting the lease.
- The standard path is `tools/release_authority.py deploy-main-commit` from an
  explicit full commit fetched from authoritative `origin/main`.
- A tool-specific failure is not permission to improvise. A break-glass plan
  requires explicit user approval, one persistent release owner, and the same
  fetched-main, clean-checkout, immutable-image, rollback, and parity evidence.
- Never use the dirty coordination root, a source archive, copied frontend
  output, or a patched live container as release source.

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
