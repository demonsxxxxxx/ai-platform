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

### s72 pinned-HTTPS bridge listener

The Docker/global callback and model bases above remain unchanged. When
`SANDBOX_CONTAINER_PROVIDER=opensandbox`, API and worker instead require these
separate, exact bridge-v1 settings:

```text
OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BASE_URL=https://REQUIRED_FIXED_EGRESS_HOSTNAME:18443
OPENSANDBOX_EXTERNAL_EGRESS_OPENAI_BASE_URL=https://REQUIRED_FIXED_EGRESS_HOSTNAME:18443/openai/v1
OPENSANDBOX_EXTERNAL_EGRESS_ANTHROPIC_BASE_URL=https://REQUIRED_FIXED_EGRESS_HOSTNAME:18443/anthropic
AI_PLATFORM_S72_BRIDGE_PORT=18443
AI_PLATFORM_S72_BRIDGE_SERVER_NAME=REQUIRED_FIXED_EGRESS_HOSTNAME
AI_PLATFORM_S72_BRIDGE_ALLOWED_SOURCE_IP=REQUIRED_S72_SOURCE_IP
AI_PLATFORM_S72_BRIDGE_TLS_CERT_FILE=<absolute-host-path-to-bridge-fullchain.pem>
AI_PLATFORM_S72_BRIDGE_TLS_KEY_FILE=<absolute-host-path-to-bridge-privkey.pem>
```

Provision a dedicated internal-CA-signed 211 leaf whose DNS SAN is exactly
`REQUIRED_FIXED_EGRESS_HOSTNAME`. Map and pin that hostname to `10.56.0.211` in
the s72 egress policy; the s72 broker connects to the IP directly and uses the
hostname only for SNI/hostname verification. Mount the full chain and private
key read-only through the two Compose paths above. Do not place certificate or
key bytes in the image, `.env`, Compose environment, logs, or Git. Provision
only the non-secret issuing CA certificate to s72 at the app-scoped path in the
s72 gateway runbook; do not install it in either host's system trust store.

The base Compose and `docker-compose.sandbox.yml` Docker rollback path do not
publish `8443`, request bridge variables, or mount bridge certificates. The
frontend image derives its 8080-only default config from the same authoritative
template. Only `docker-compose.opensandbox.yml`, after every bridge prerequisite
below is present, selects the full template and adds the bridge configuration.

The frontend keeps its existing `8080` public listener unchanged. The additional
container `8443`/host `18443` TLS listener has access logging disabled. Its
default TLS server rejects the handshake for missing or wrong SNI; only the exact
configured SNI reaches the named server, which separately requires the matching
HTTP Host and configured s72 source IP. It exposes only:

- exact `POST /api/ai/runtime/callbacks/executor` to the existing API upstream;
- `/openai/...` to host model port `3002`, stripping only `/openai/`;
- `/anthropic/...` to host model port `3002`, stripping only `/anthropic/`;
- `GET /healthz`.

Every other path is `404`; disallowed methods/sources fail closed. There is no
static frontend, redirect, arbitrary upstream, or credential log on this
listener. `host.docker.internal:host-gateway` is only frontend-to-host model
reachability; it does not grant the sandbox network access. The OpenSandbox
sandbox remains `network_mode=none` and reaches the host broker only through the
scoped regular-file relay.

Before switching the provider, validate on the Docker-capable host and from the
approved s72 source without printing authorization values:

```bash
sudo -n docker compose --env-file deploy/ai-platform/.env \
  -f deploy/ai-platform/docker-compose.yml \
  -f deploy/ai-platform/docker-compose.opensandbox.yml config >/dev/null
sudo -n docker exec ai-platform-frontend nginx -t
curl --fail --silent --show-error \
  --resolve REQUIRED_FIXED_EGRESS_HOSTNAME:18443:10.56.0.211 \
  --cacert /etc/opensandbox-gateway/tls/upstream-ca.pem \
  https://REQUIRED_FIXED_EGRESS_HOSTNAME:18443/healthz
```

Also prove from s72 that an unlisted path is `404`, missing/wrong SNI fails the
TLS handshake even with the expected HTTP Host, Host mismatch fails after the
right SNI, wrong-CA checks fail, each model prefix reaches the expected `/v1/...` path with authorization
preserved but absent from logs, the exact callback succeeds, and requests from
any other source IP are denied. These are current runtime gates; local source
tests do not satisfy them. If any gate fails, keep the Docker provider selected.

Use the standard release authority with the OpenSandbox overlay only after those
gates pass; the placeholders below are contracts, not real paths, images,
commits, or credentials. The
authority retains the `ai-platform-phaseb` Compose project, resolves the
immutable executor image itself, and is the only permitted mutation path:

```bash
cd <release-root>
python3 tools/release_authority.py deploy-main-commit \
  --release-root <release-root> \
  --commit <40-lowercase-hex-commit> \
  --strategy auto \
  --docker-cmd "sudo -n docker" \
  --env-file <release-root>/deploy/ai-platform/.env \
  --compose-file deploy/ai-platform/docker-compose.yml \
  --compose-file deploy/ai-platform/docker-compose.opensandbox.yml
```

Docker selection and rollback continue to use exactly the base plus Docker
sandbox overlay, with no bridge certificate or variable dependency:

```bash
python3 tools/release_authority.py deploy-main-commit \
  --release-root <release-root> \
  --commit <40-lowercase-hex-commit> \
  --strategy auto \
  --docker-cmd "sudo -n docker" \
  --env-file <release-root>/deploy/ai-platform/.env \
  --compose-file deploy/ai-platform/docker-compose.yml \
  --compose-file deploy/ai-platform/docker-compose.sandbox.yml
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

`--strategy auto` verifies current Compose ownership, role image labels, embedded
runtime provenance, and the immutable sandbox executor reference before it
classifies `current-runtime..target`. It then emits compact, redacted per-stage
strategy/action/wall-time evidence:

- role dependency manifests select that role's canonical build;
- backend source-only changes select the runtime-only rebase, which copies the
  exact target source and rewrites source markers without APT, pip, or pnpm;
- frontend source-only changes use the cached dependency stage and rebuild only
  the frontend source stage;
- unchanged roles promote the already verified current role image to exact target
  labels and embedded provenance; deployment-only changes rebuild neither role;
- a rerun for the same target and Compose project reuses verified target images,
  then converges Compose with `--no-build`.

The following established offline and runtime-only recovery safeguards remain
part of that same release-authority path; they do not authorize an alternate
Compose deployment:

- Do not make a smoke check depend on Docker Hub. For sandbox cancel probes,
  prefer an already-local image such as `ai-platform:local` via
  `--cancel-image ai-platform:local`.
- The committed Compose file intentionally does not forward package-index
  variables as build arguments. When dependencies have not changed and package
  download fails, rebuild the local runtime image from the current or backup
  image by copying only `pyproject.toml`, `app/`, `skills/`, and
  `docker-entrypoint.sh`, then recreate with `--no-build`. The auto backend
  runtime rebase additionally clears and replaces every target runtime subject
  before it updates exact provenance, so deleted target files cannot survive.
- Runtime-only images prepared from a Git archive or Windows snapshot must run
  `chmod +x /app/docker-entrypoint.sh` before container recreation.
- If repeated runtime-only rebases fail with Docker `max depth exceeded`, stop
  stacking layers. Flatten the current healthy container with `docker export`
  and `docker import`, build once from that flat base, and re-run provenance,
  health, and target-path verification.

Backend source-only/runtime-overlay stages are bounded at 90 seconds and frontend
source-only stages at 180 seconds. Dependency-triggered canonical builds have a
separate bounded 900-second bootstrap maximum; this does not widen either
source-only SLO. On timeout the authority terminates its owned process tree and
uses a short bounded pipe-drain grace before reporting the redacted failure.
Retain the compact stage evidence and do not expose the environment file or raw
command output. The existing external lease/fencing gate remains the only overlap
guard.

The local workstation does not provide Docker. The real-211 benchmark gate must
observe backend-only auto release below 90 seconds, frontend-only below 180
seconds, and deployment-only change with zero role builds before those timings
are claimed as passed.

## Terminal Evidence

A release is complete only after the owner reports the exact commit and image,
container identity and restart counts, API/frontend health, relevant smoke,
rollback subject, authority terminal state, and final source/runtime parity.
Historical evidence or a healthy old runtime does not prove the target release.
