# 211 Release Operations Runbook

This runbook contains host commands, recovery paths, and terminal evidence for
ai-platform releases on the Docker-capable 211 host. Product/source boundaries
live in the guardrails; task ownership, readiness, leases, and break-glass
authority live in `docs/agent-rules/multi-agent-context-workflow.md`.

## Canonical Exact-Main Command

The normal 211 release uses exactly this Git-native authority flow with the base
Compose file and Docker sandbox overlay. Run it only after read-only readiness
has passed and exactly one project-bound release owner holds the single mutation
lease. It does not grant a lease or replace the workflow gates. Resolve `SOURCE`
and `ROOT` from the current 211 host mapping in
`docs/agent-rules/ai-platform-guardrails.md`, the authoritative source for those
host subjects.

```bash
set -eu
: "${SOURCE:?set SOURCE to the guardrails-designated 211 coordination checkout}"
: "${ROOT:?set ROOT to the guardrails-designated 211 managed release root}"
git -C "$SOURCE" fetch --no-tags origin main:refs/remotes/origin/main
TARGET="$(git -C "$SOURCE" rev-parse refs/remotes/origin/main)"
cd "$SOURCE"
python3 tools/release_authority.py deploy-main-commit \
  --release-root "$ROOT/releases" \
  --commit "$TARGET" \
  --strategy auto \
  --docker-cmd "sudo -n docker" \
  --compose-file deploy/ai-platform/docker-compose.yml \
  --compose-file deploy/ai-platform/docker-compose.sandbox.yml
```

`SOURCE` is the coordination checkout: it supplies the authority executable and
the freshly fetched authoritative main ref, but it is never the Docker build
context for the target release. Its tracked, staged, and ordinary untracked
state must be clean. Ignored-only artifacts such as
`tools/__pycache__/release_authority.cpython-312.pyc` are allowed, are not copied,
and do not affect the fetched Git object or immutable target checkout.

`ROOT` is the managed release root contract. `--release-root` must be the
normalized absolute `$ROOT/releases` directory. The authority derives the
operator-held env file as `$ROOT/deploy/ai-platform/.env`; a missing
`$SOURCE/deploy/ai-platform/.env` is irrelevant. Do not add `--env-file` in the
normal flow. The compatibility `--env-file` override must equal that exact
canonical path after normalization; an external file is rejected even if it has
the same owner and mode. The canonical file must be an existing regular
non-symlink owned by the managed-root owner with mode `0600`. The authority
validates metadata before target materialization and again before Compose
mutation; it never reads, copies, or prints contents.

`$ROOT/releases/$TARGET` is the immutable target checkout and the only target
build context. Its HEAD, tracked/staged/ordinary untracked state, ignored-file
set, path/link boundaries, and fetched-main provenance remain strictly
fail-closed. The managed-root owner must own `$ROOT/releases`, the exact checkout,
and every regular file and directory in the materialized checkout, including its
local Git metadata; those paths must be non-links and not group- or
world-writable. An existing checkout passes this filesystem trust gate before
any Git command or fetch can read its config or mutate its remote objects. Only
after that local trust gate does the authority require exact HEAD, clean and
ignored-file state, fetch main, and revalidate fetched-main provenance plus the
exact Git tree. That commit/tree is the tracked-source manifest; tracked
symlinks and non-regular entries are rejected, and there is no separate manifest
artifact. Coordination ignored-file allowance never applies there.

For governed Docker egress, the command resolves the reviewed backend build to
its local immutable `sha256:<64-hex>` Docker image ID and passes that value as
`SANDBOX_EXECUTOR_IMAGE` to API and worker. Do not replace that handoff with a
mutable `ai-platform:<commit>` tag; an operator must rebuild or resolve the
target image ID before retrying when it is unavailable locally.

## Governed Sandbox Overlay Contract

At `<managed-root>/deploy/ai-platform/.env`, the operator-held environment file
must set the exact release subject and the governed callback boundary without
recording a raw key in terminal evidence:

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
sudo -n docker compose --env-file "$ROOT/deploy/ai-platform/.env" \
  -f "$ROOT/releases/$TARGET/deploy/ai-platform/docker-compose.yml" \
  -f "$ROOT/releases/$TARGET/deploy/ai-platform/docker-compose.opensandbox.yml" config >/dev/null
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

Use the same release authority with the OpenSandbox overlay only under an
explicit provider-transition release charter after those gates pass. The normal
exact-main command above remains base plus Docker sandbox, with no bridge
certificate or variable dependency. The authority retains the
`ai-platform-phaseb` Compose project, resolves the immutable executor image
itself, and remains the only permitted mutation path.

The release authority permits one exact provider-overlay ownership transition:
the live selection `[docker-compose.yml, docker-compose.sandbox.yml]` may move to
`[docker-compose.yml, docker-compose.opensandbox.yml]`, and the exact reverse is
permitted for Docker rollback. It reconstructs the live selection only from the
three containers' Compose labels and the existing historical checkout, requires
the same project, role/container identity, release root, and complete ordered
selection for API, worker, and frontend, and revalidates that ownership before
Compose mutation. This is not manual adoption. A base-only, reordered, duplicate,
missing, extra, or arbitrary overlay selection, a caller-selected substitute,
another project/root/role, or any symlink, junction, or path escape fails closed.
After `compose up`, terminal parity still requires every managed role to report
the exact target checkout and target two-file selection.

Before allowing untrusted execution, verify that the API is healthy with the
same runtime commit, that each lease bridge contains only that API witness and
its sandbox, and that the proof key material is present but never printed.

## Readiness Evidence

Before the workflow grants its release lease, the read-only host packet must
identify the publisher and target commits, host and runtime subject, executable
rollback subject, release-authority state and lock holder, Docker/Compose
capability, coordination-source tracked/staged/ordinary-untracked cleanliness,
managed env presence/link/owner/`0600` metadata without contents, strict target
checkout status including ignored content, managed-root ownership and
non-group/world-writable mode for its tracked Git tree, per-service ownership and
recover/adopt compatibility, and the exact services and method that require
mutation. Missing or stale fields block release work rather than becoming
discovery work inside a mutation task. A terminal gate failure is corrected by
having the managed owner provision the canonical env file or a new immutable
target checkout, or by using a separate clean exact-main coordination checkout;
it never instructs an operator to delete or clean observed source content.

## Host Command Rules

- Invoke repository Python checks with `python3`; bare `python` is Python 2.7 on
  the host.
- Verifiers that need Docker use `--docker-cmd "sudo -n docker"`.
- `sudo` does not preserve a leading environment assignment. Select a Compose
  image with `sudo -n env AI_PLATFORM_IMAGE=<tag> docker compose ...`, not
  `AI_PLATFORM_IMAGE=<tag> sudo -n docker compose ...`.
- Do not read, copy, export, or quote the real deployment `.env`. Pass it only
  through the canonical authority's derived managed path and report redacted
  metadata evidence.

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
