# Release Operations Runbook

This runbook contains host commands, recovery paths, and terminal evidence for
ai-platform releases on an operator-approved Docker-capable host. Product and
security boundaries live in repository code, tests, architecture decisions, and
`AGENTS.md`; task ownership, readiness, leases, and break-glass authority live in
`docs/agent-rules/multi-agent-context-workflow.md`.

## Canonical Exact-Main Command

The normal release uses exactly this Git-native authority flow with the base
Compose file and Docker sandbox overlay. Run it only after read-only readiness
has passed and exactly one project-bound release owner holds the single mutation
lease. It does not grant a lease or replace the workflow gates. The operator must
provide absolute `SOURCE` and `ROOT` paths for the current controlled host; the
repository does not hard-code a server identity or host filesystem layout.

Resolve `SOURCE` to the operator-approved clean coordination checkout and
`ROOT` to the operator-approved managed release root before running either
command below. The operator must verify both paths and the target main ref.

### Governed Debian mirror preflight and no-deploy probe

The following is the explicit controlled-host operator example. The release authority accepts
only this complete pair of HTTPS endpoints; it records only their normalized
hostnames in safe release evidence. The product and Docker daemon do not hard-code
this vendor choice.

```sh
set -eu
: "${SOURCE:?set SOURCE to the operator-approved coordination checkout}"
export APT_MIRROR="https://mirrors.ustc.edu.cn/debian"
export APT_SECURITY_MIRROR="https://mirrors.ustc.edu.cn/debian-security"
: "${ROOT:?set ROOT to the operator-approved managed release root}"
: "${TARGET:?set TARGET to the exact fetched main commit}"
python3 -B "$SOURCE/tools/release_authority.py" probe-apt-mirrors \
  --apt-mirror "$APT_MIRROR" \
  --apt-security-mirror "$APT_SECURITY_MIRROR" >/dev/null
```

The authority probe uses HTTPS GET with a bounded Range covering the complete
InRelease (up to 256 KiB), then verifies Content-Range/Content-Length and the
complete clear-signed PGP envelope. Release identity is the exact Codename;
Suite accepts Debian lifecycle aliases stable, oldstable, or oldoldstable, with
the matching `-security` suffix for the security endpoint. It rejects unsafe
redirects, unknown or oversized/truncated responses, invalid content, timeouts,
and non-2xx status.
After the read-only endpoint checks, this bounded Docker probe exercises the canonical
backend Dockerfile dependency layer only. It does not invoke Compose, recreate a
service, or deploy an image; remove the temporary probe image after inspection.

```sh
PROBE_IMAGE="ai-platform:apt-mirror-probe-${TARGET}"
sudo -n docker build \
  --build-arg "AI_PLATFORM_BUILD_COMMIT=${TARGET}" \
  --build-arg AI_PLATFORM_BUILD_DIRTY=false \
  --build-arg AI_PLATFORM_BUILD_REPOSITORY=https://github.com/demonsxxxxxx/ai-platform.git \
  --build-arg "APT_MIRROR=${APT_MIRROR}" \
  --build-arg "APT_SECURITY_MIRROR=${APT_SECURITY_MIRROR}" \
  -t "$PROBE_IMAGE" \
  -f "$ROOT/releases/$TARGET/Dockerfile" \
  "$ROOT/releases/$TARGET"
sudo -n docker image rm "$PROBE_IMAGE"
```

```bash
set -eu
: "${SOURCE:?set SOURCE to the operator-approved coordination checkout}"
: "${ROOT:?set ROOT to the operator-approved managed release root}"
umask 077
git -C "$SOURCE" fetch --no-tags origin main:refs/remotes/origin/main
TARGET="$(git -C "$SOURCE" rev-parse refs/remotes/origin/main)"
if test -n "$(git -C "$SOURCE" status --porcelain --untracked-files=all)"; then
  printf '%s\n' "coordination source must be clean before exact-main checkout" >&2
  exit 1
fi
git -C "$SOURCE" checkout --detach "$TARGET"
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$TARGET"
test -z "$(git -C "$SOURCE" status --porcelain --untracked-files=all)"
cd "$SOURCE"
MIRROR_ARGS=()
if test -n "${APT_MIRROR:-}" || test -n "${APT_SECURITY_MIRROR:-}"; then
  : "${APT_MIRROR:?set APT_MIRROR to the reviewed HTTPS Debian archive endpoint}"
  : "${APT_SECURITY_MIRROR:?set APT_SECURITY_MIRROR to the reviewed HTTPS Debian security endpoint}"
  PYTHONPATH="$SOURCE/tools" python3 -B -c 'import os; from release_authority import _normalize_apt_mirror_pair; _normalize_apt_mirror_pair(os.environ["APT_MIRROR"], os.environ["APT_SECURITY_MIRROR"])'
  MIRROR_ARGS=(--apt-mirror "$APT_MIRROR" --apt-security-mirror "$APT_SECURITY_MIRROR")
fi
timeout --signal=INT --kill-after=30s 24000s \
  python3 -B tools/release_authority.py deploy-main-commit \
  --release-root "$ROOT/releases" \
  --commit "$TARGET" \
  --strategy auto \
  --canonical-build-timeout-seconds 1800 \
  "${MIRROR_ARGS[@]}" \
  --docker-cmd "sudo -n docker" \
  --compose-file deploy/ai-platform/docker-compose.yml \
  --compose-file deploy/ai-platform/docker-compose.opensandbox-internal-test.yml
```

`SOURCE` is the coordination checkout: it supplies the authority executable and
the freshly fetched authoritative main ref, but it is never the Docker build
context for the target release. Before the authority starts, the canonical command
requires clean tracked, staged, and ordinary untracked state, detaches `SOURCE` at
the exact fetched `TARGET`, and proves both `HEAD == TARGET` and clean ordinary
status. A dirty source fails; this command never cleans, resets, stashes, or
otherwise preserves source changes. Ignored-only artifacts such as
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
mutation. The Python authority never reads or copies the file contents. Its
bounded Docker Compose preflight receives the file only as `--env-file`, suppresses
resolved output and raw parser errors, and never records the values in evidence.

The managed environment must set `CORS_ALLOW_ORIGINS` to the exact
browser-visible frontend origin, including scheme and any non-default host port
(the base Compose frontend port defaults to `18001`). Container-internal port
`8080`, localhost development origins, and retired host addresses are not valid
production substitutes. The API and worker both require this value explicitly,
so Compose preflight fails before mutation when it is absent.

### Secure provisioning and read-only Compose semantic preflight

Before authorizing an expensive image build or deployment, the managed owner must
securely provision every required key for the exact selected Compose files. Do not
invent defaults for an authority, attestation, egress, runtime-subject, credential,
or TLS-path input. The canonical release authority then runs Docker Compose's own
semantic `config --quiet` parser before any container ownership inspection, image
lookup/build/tag, manual-container removal, or `compose up`. Image references that
do not exist until after a build are replaced only inside that read-only parser
process with a fixed `.invalid` preflight placeholder; the convergence command is
constructed separately from verified target image references.

For a direct OpenSandbox selection, do not render resolved config manually. The
release authority uses Compose JSON rendering to verify API/Worker provider
parity, direct SDK mode, governed profile, bridge networking, proxy binding,
model-proxy token presence, and reset host ports. The same-project transition
keeps `ai-platform-internal` and its exact named volumes; never invokes a
project migration or volume aliases. On failure, keep deployment authorization
blocked and use only the authority's bounded key-name diagnostic; never print
or copy the managed environment file.
The authority permits at most 32 distinct required keys and at most 33 parser
attempts. Each parser attempt is capped at 15 seconds. When one or more required
keys are absent, it reports only the fixed `missing-required-config` category and
sorted key names. Invalid Compose, an unrecognized parser failure, or a larger
required-key surface fails closed without returning raw stderr, commands, paths,
or values.

To roll back the mirror choice while keeping the same release authority, leave both
mirror variables unset and rerun the canonical invocation: `MIRROR_ARGS` stays empty
and the CLI omits both mirror flags. Supplying only one option is invalid; omitting
the pair preserves the upstream Debian endpoints. The mirror preflight/probe block
above is only required when selecting a mirror pair.

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

### Internal-test direct OpenSandbox functional acceptance

`docker-compose.opensandbox.yml` is the single direct-OpenSandbox overlay. It
pins API and worker to `SANDBOX_CONTAINER_PROVIDER=opensandbox`,
`SANDBOX_SECURITY_PROFILE=governed`,
`OPENSANDBOX_EXPECTED_NETWORK_MODE=bridge`, native server proxying, and the
stateless model/callback egress entry. Use it only with the base Compose file;
do not combine it with the Docker-socket sandbox overlay.

This mode connects directly to the official OpenSandbox lifecycle API. It does
not require a lifecycle gateway, mailbox relay, custom `/attestation` endpoint,
or a provider-specific credential path. Model traffic uses the existing
platform model control plane through the stateless Nginx egress entry. The
OpenSandbox API key remains an operator-held secret and must never appear in
commands, logs, or evidence.
Unknown profiles, production selection, non-OpenSandbox providers, or a
one-sided network-mode change fail during process startup.

For the internal-test direct OpenSandbox release, the latest-main quickstart resolves the
current fixed-repository `main` SHA and waits up to 30 minutes for successful
backend, frontend, and packaging `push` runs for that exact SHA. It also requires
the `backend required`, `frontend required`, and `release image ready manifest`
jobs to have completed successfully. The private repository requires
`GH_TOKEN`/`GITHUB_TOKEN` with repository Contents and Actions read access, or an
authenticated local `gh` CLI session. The Docker host must already be logged in
to `ghcr.io`.

The quickstart downloads only the packaging ready artifact whose name is derived
from the exact SHA, workflow run ID, and run attempt. It applies bounded archive
extraction and runs the target checkout's semantic release-manifest verifier over
the manifest, SBOM, provenance, signature, and scan evidence. Only then does it
atomically prepare `/data/ai-platform-internal-test/incoming/latest-main.json` as
a non-secret owner-managed file with exactly these fields:

```json
{
  "source_commit": "<fresh-main-40-hex-sha>",
  "backend_image": "ghcr.io/demonsxxxxxx/ai-platform-backend@sha256:<digest>",
  "frontend_image": "ghcr.io/demonsxxxxxx/ai-platform-frontend@sha256:<digest>",
  "env_file": "/data/ai-platform-internal-test/config/<managed-subject>/.env",
  "ci_success": true
}
```

For an existing deployment, the controller reuses the stable managed `.env` path
from the current subject. It materializes the matching exact-main checkout at
`/data/ai-platform-internal-test/releases/<source_commit>` and completes the
deployment with one command from any trusted checkout containing this wrapper:

```bash
./scripts/deploy-latest.sh --profile internal-test --latest
```

On the first deployment, supply the managed path once with
`--env-file /data/ai-platform-internal-test/config/<managed-subject>/.env`, or set
`AI_PLATFORM_QUICKSTART_ENV_FILE` for that invocation. A controller that has
already prepared the exact checkout and subject may still use the compatible
`./scripts/deploy-latest.sh --profile internal-test` retry path.

One owner-managed advisory lock covers Actions discovery, exact checkout
materialization, artifact verification, subject replacement, image pull,
Compose mutation, health checks, and rollback. The quickstart removes GitHub API
credentials from the child deployment environment, rechecks fresh
`origin/main`, requires immutable role-specific image refs, validates the
existing runtime-scoped managed `.env` as an owner-matching `0600` regular file
without reading or printing its values, and runs Compose semantic preflight
before either pull or up. It uses only the base file plus
`docker-compose.opensandbox-internal-test.yml` and never builds on the managed
host. It never runs `down`, `down -v`, or volume deletion. If startup or smoke
fails,
it first stops target API admission and the target worker, then atomically checks
both queue processing and retry metadata for protocol-v2 leases. It performs one
`--no-build --pull never` up of the saved previous subject only when that check
proves no v2 lease remains. If any processing or retry-only v2 lease remains, or
the check is inconclusive, the API stays stopped, the exact target worker is
restarted to recover stored work, and image rollback stops for operator action.
Recovery is accepted only after the container retains the exact target commit,
image, Compose identity, container identity, restart count, and process identity
across two advancing fresh runtime heartbeats. Termination signals received after
the target runtime transition begins are deferred until the target, its recovery
worker, or the previous runtime passes its complete health gate.
Postgres, Redis, MinIO, and workspace volumes remain untouched.

The target and saved previous runtime use the same worker gate: two fresh,
advancing process heartbeats with stable container, restart, configuration, and
process identity. API readiness plus a merely running worker container is not a
successful release or rollback. The quickstart installs one stateful termination
policy before preflight, isolates child commands in their own process sessions,
and begins deferring termination before the target `up`; repeated signals are
honored only after one runtime has passed its complete gate.

The backend artifact also contains the OpenSandbox executor application. The
quickstart binds `OPENSANDBOX_EXECUTOR_IMAGE` and its digest to that exact
immutable backend subject, pulls it on the internal-test Docker host before
startup, and requires a successful local image inspection. This is a host-side
cache warmup,
not an OpenSandbox fork or server modification. A multi-node OpenSandbox runtime
must perform the same digest-bound pull on every node that may create a sandbox.

Skill packages are stored only in the private `run_skill_materializations`
table. Run input and Redis carry bounded digest-bound references; create, copy,
retry, and resume reject full or mixed manifest payloads. This is a hard
cutover: unfinished legacy Skill runs without private materializations cannot be
resumed after deployment and must be submitted again.

The durable queue-heartbeat schema activation also has a clock-safety gate. It
stops with `run_attempt_future_heartbeat_requires_remediation` when an open
attempt heartbeat is more than five seconds ahead of the PostgreSQL clock. Treat
that as a deployment blocker: stop admission, preserve the database and Redis
lease evidence, and identify the affected rows with a read-only query before an
operator-approved lifecycle recovery. Do not install the monotonic guard over
those rows. Forward mixed-version operation is bounded because protocol-v2
leases are opaque to the v1 reclaimer; image rollback to a v1-only worker is
allowed only after current workers have drained or recovered every protocol-v2
processing or retry-only lease. The automatic quickstart enforces the same gate;
after its recovery worker drains the queue, rerun the release or perform an
operator-approved recovery before retrying image rollback.

`ci_success` is written only after exact-run Actions and packaging evidence
verification. Keep the selected managed env path stable across successive
releases. A failure before subject replacement preserves the previous subject
byte-for-byte. A deployment failure after admission keeps the approved target
subject for an explicit retry. The small image rollback proves that the previous
images became healthy again. Every schema
change admitted to this path must also preserve the saved previous binary's exact
schema-readiness contract; the real PostgreSQL compatibility test installs that
exact base before applying the candidate schema.

Before running, the internal-test host must be able to reach both GitHub and
GHCR through the operator-approved proxy. The quickstart only inherits standard proxy
environment behavior; it does not configure Git, Docker daemon, or host proxy
settings.

Its short API/ready/container/OpenSandbox health result is deployment smoke,
not the application-owned OpenSandbox lifecycle acceptance described below.

The `bridge` network is an accepted internal-test risk, not production
isolation evidence. Acceptance still requires one application-owned run to
prove SDK create and metadata readback, executor health and runtime identity,
command execution, file stage/read/collect, stop, and orphan-free cleanup. On
the internal-test host, inspect that exact sandbox container and record
`HostConfig.Runtime=runsc`; configuration files and source tests are not runtime
proof. Keep the governed profile as the production default and track network
closure as separate follow-up work.

The production release uses the base Compose file plus
`docker-compose.opensandbox.yml`. API and Worker use the official
OpenSandbox SDK directly with `OPENSANDBOX_USE_SERVER_PROXY=true`, but do not
send an OpenSandbox `networkPolicy`: OpenSandbox rejects that policy when the
secure runtime is gVisor. The independently managed trusted OpenSandbox Server
container uses host networking so it can proxy to Executor IPs, while its
root-owned TOML binds the HTTP listener and `docker.host_ip` to the exact private
lifecycle address. Spawned sandboxes retain `runsc` and use Docker network
`ai-platform-opensandbox-egress-internal-v1`. That internal bridge has IP
masquerading and inter-container connectivity disabled, uses subnet
`172.31.75.0/24`, and has the fixed host interface `br-osb-egress`; only the
dual-homed `opensandbox-egress-proxy` service joins it at `172.31.75.2`, under
alias `egress.opensandbox.internal`, and the proxy publishes no host port.

The repository-owned `ai-platform-opensandbox-network-guard.service` installs
the first host INPUT jump for `br-osb-egress`. Its exact chain accepts only
`ESTABLISHED,RELATED` return traffic for host-initiated Server-to-executor
connections and drops sandbox-initiated host traffic. Its first `DOCKER-USER`
jump also permits only sandbox-to-proxy TCP `8080` traffic and its established
replies before dropping every other same-bridge flow. This preserves Server
proxying without exposing lifecycle, management, or other host listeners to a
sandbox. Target parity authenticates the live Server TOML, Server host-network
identity and listener binding, guard rule order, Docker network options and
labels, and the egress proxy as the network's sole steady-state member. The
proxy strips sandbox credentials, forwards the callback-derived per-attempt
model capability, injects the internal proxy token, and forwards callbacks to
the existing API callback-token validators. Direct-OpenSandbox releases bind
the
Executor to the Packaging-qualified Backend `repository@sha256` reference and
bind its digest field to the matching `sha256` value; target parity resolves that
reference and requires the resulting image ID to equal the API and Worker image.

### Production rebuild and direct-runtime update

The production bootstrap command is the normal recovery entry when the managed
production host has no AI Platform Compose containers, and the normal
image-update entry after it is already running the verified direct-OpenSandbox
contour. The legacy Docker-sandbox-to-OpenSandbox transition is documented
separately below.

The base host must already provide Python 3.11 or newer, Docker with Compose v2,
systemd, and a registered Docker `runsc` runtime. Root must have private GHCR
pull access and either `GH_TOKEN`/`GITHUB_TOKEN` with repository Contents and
Actions read access or an authenticated `gh` CLI session. Before the command,
restore these files through the approved secret-management path:

```text
/data/ai-platform-prod/config/production/.env        root:root 0600
/etc/ai-platform/opensandbox/server.env              root:root 0600
/etc/ai-platform/opensandbox/server.toml             root:<server-gid> 0640
```

`<server-gid>` must exactly equal `OPENSANDBOX_SERVER_GID`. The server runs as
that dedicated non-root identity, so this bounded group-read permission is what
allows it to read the mounted TOML; the application env and systemd environment
file remain root-only.

Use `deploy/opensandbox/server-production.env.example` and
`deploy/opensandbox/server-production.toml.example` only as initial schemas.
Replace every required placeholder before sealing the files. The application env must
set `SANDBOX_WORKSPACE_ROOT=/data/ai-platform-prod/runtime-workspaces`, the
production direct-OpenSandbox keys required by the selected overlay, and the
same lifecycle API key and lifecycle endpoint provisioned for the host service.
Keep those two values plain and unquoted in the application env so the bootstrap
can compare them without sourcing the file.
The application `OPENSANDBOX_EXECUTOR_IMAGE` is separately bound to the
release-authority backend workload image; it is not the OpenSandbox host TOML's
`runtime.execd_image`. Do not print, copy, source, or pass secret values on the
command line.

The server, execd, and egress sidecar references must all be immutable digests.
Before sealing `server.env`, independently verify the exact server digest against
an approved OpenSandbox `server/v0.1.13` or newer release; the upstream server
image does not expose a trustworthy source-version OCI label. The reviewed TOML
sets `[server].host` and `docker.host_ip` to the lifecycle address, binds
sandboxes to `ai-platform-opensandbox-egress-internal-v1`, retains `dns+nft`
only as the pinned upstream sidecar configuration, disables IPv6 egress, and
sets `allowed_host_paths = []`, `sandbox_binds = []`, and `sandbox_env = {}`.
Application requests omit the incompatible OpenSandbox SDK `networkPolicy`.
This application transfers files through the SDK and never sends a host bind
mount.

For a cold rebuild, run:

```bash
cd "$SOURCE"
sudo -n ./scripts/deploy-latest.sh --profile production --latest \
  --env-file /data/ai-platform-prod/config/production/.env
```

The controller requires root, secure canonical config metadata, Docker Compose,
`runsc`, one private lifecycle address, the exact active host INPUT guard from
the target checkout, digest-bound OpenSandbox server, execd, and egress images,
and a matching Docker socket group. The lifecycle address must already be
assigned to the production host. It creates or validates the canonical
server-state and platform-workspace directories, installs the exact checkout's
reviewed `opensandbox.service`, pulls its immutable images, and proves
service/container identity plus `/health`. It then reuses the exact-main Actions
admission, materializes `/data/ai-platform-prod/releases/<commit>`, validates the
production Compose semantics, and converges project `ai-platform-internal` with
the base file plus `docker-compose.opensandbox.yml`.

Treat the OpenSandbox server as part of the trusted host control plane. The
read-only filesystem bind for `/var/run/docker.sock` does not attenuate Docker
API permissions: the server still has effective Docker daemon authority. The
root-owned repository-managed unit, exact unit-to-container source label,
container host-network identity, exact private listener binding, and host INPUT
guard are the admission boundary; they do not replace application-owned runsc
and sandbox-lifecycle acceptance. The trusted OpenSandbox server container uses
host networking so it can proxy to Executor container IPs, but the root-owned
TOML binds it only to the private lifecycle address. `runsc` is selected by its
for spawned executor/sandbox containers and must be observed on those real
sandbox containers during acceptance. The unit's isolated guard removes the
fixed-name server container only after verifying its immutable image reference,
source commit, and repository ownership labels; a foreign same-name container
blocks start or stop.

After the first successful deployment, the same command reuses the approved env
path:

```bash
sudo -n ./scripts/deploy-latest.sh --profile production --latest
```

An existing direct production update first proves exact current parity,
quiescence, no managed sandbox containers, and schema equality needed by the
image rollback path. It also requires an already installed bootstrap-managed
OpenSandbox unit whose recorded host-configuration fingerprint exactly matches
the current parsed `server.env` and TOML. Routine application updates never adopt
a foreign service or change the Server image, API key, execd/egress images,
identity, addresses, or policy. Treat those as a separately reviewed host
maintenance operation. Target startup or parity failure performs one
restore from the exact previous checkout and verified local images only after
admission is stopped again and quiescence is re-proved. If the reviewed host unit
changed, its previous version is restored and revalidated before the previous
application images start. If that rollback fence is unavailable, the controller
leaves admission stopped and does not start the previous images. A cold start has
no application runtime to restore; failure preserves all named volumes and
removes the newly created Compose containers and networks after fencing
admission. A cleanup failure stops for bounded operator inspection. Never use
this command to adopt a partial, foreign, or legacy Compose contour. Use the
explicit transition below for a live legacy production installation.

A nonzero run may be retried from the already admitted subject, without another
Actions download, only after its failure has been classified:

```bash
sudo -n ./scripts/deploy-latest.sh --profile production
```

Successful completion proves host-service health, application deployment smoke,
and exact production parity. It still reports application-owned OpenSandbox
acceptance as pending. Complete the create -> execute -> stage/read/collect ->
delete and `HostConfig.Runtime=runsc` checks below before production acceptance.

The production deployment keeps Compose project `ai-platform-internal` and the
four existing named data/workspace volumes. It also preserves the authenticated
`/data/ai-platform-prod/runtime-workspaces` platform bind for `workspace-init`,
API, and Worker; OpenSandbox sandboxes never receive that host path and continue
to use bounded SDK file transfer. A bounded transition stops admission,
proves zero nonterminal Runs/RunAttempts/leases and zero sandbox containers,
changes the overlay in place, and revalidates exact volume and bind mount
identities. Rollback uses the same project and volumes, plus the same platform
bind, without
`down -v` or any project namespace migration.

Run the transition only from the exact CI-qualified target checkout.
`LEGACY_REPO_ROOT` must likewise be a newly materialized, root-owned exact-commit
checkout used only as rollback authority; it need not be the old deploy-user
checkout recorded in
the running containers. The transition validates initial container labels
against the fixed historical production release root, commit, and Compose
relative paths without reading or executing that old tree. After the transition restores
the project from the authenticated rollback checkout, a retry accepts only a
consistent label set naming that same trusted Compose selection. Preserve the
currently verified Docker release values before cutover; they are rollback
arguments, not values to
rediscover after a failure. The command exits before mutation when schema,
quiescence, image, project, volume, host, guard, isolated-network, or Compose
preflight fails. A failure after the old contour stops performs one automatic
rollback and exits nonzero.

Before migration, require zero active sandboxes. Install the exact guard unit from
the qualified checkout at
`/etc/systemd/system/ai-platform-opensandbox-network-guard.service` only when
the destination is absent or byte-identical; the installed file must remain
root-owned, regular, and not group- or world-writable. A different existing unit
is a stop condition, not overwrite authority. Enable and start that guard before
converging the repository-managed production OpenSandbox host service. The
sealed `/etc/ai-platform/opensandbox/server.env` and `server.toml` must satisfy
the production bootstrap contract above: one private lifecycle address, exact
Server listener binding, host-network Server container, fixed internal sandbox
network, and no application SDK `networkPolicy`. Existing foreign or native
OpenSandbox units require an explicitly approved host-maintenance replacement;
the bootstrap never adopts or overwrites them silently. Confirm the exact guard,
bootstrap-managed OpenSandbox unit, and `/health` before invoking the transition.
The target migration preflight independently repeats the configuration, guard,
and live-network checks.

```bash
cd "$TARGET_REPO_ROOT"
umask 077
sudo -n python3 -B -m tools.s75_opensandbox_transition migrate \
  --target-repo-root "$TARGET_REPO_ROOT" \
  --target-commit "$TARGET_COMMIT" \
  --legacy-repo-root "$LEGACY_REPO_ROOT" \
  --legacy-commit "$LEGACY_COMMIT" \
  --env-file "$MANAGED_ENV_FILE" \
  --backend-image "$TARGET_BACKEND_IMAGE" \
  --frontend-image "$TARGET_FRONTEND_IMAGE" \
  --docker-cmd docker >"$TRANSITION_RESULT"
```

Do not finalize from the migration command alone. Require all eight application
services plus `opensandbox-egress-proxy` healthy, API readiness and schema checks,
zero restart-count growth, exact target image/Compose parity, unchanged volume
IDs and mounts, and a real application-owned Run that proves
create -> execute -> collect -> delete through OpenSandbox with
`HostConfig.Runtime=runsc`, no SDK `networkPolicy`, and exact membership in the
isolated network. From that sandbox, the egress proxy must remain reachable,
while the lifecycle listener, API, PostgreSQL, Redis, MinIO, another active
sandbox, another host port, an unrelated private address, and a public-internet
address must all be unreachable. That Run must also prove model traffic through
the existing model-control-plane proxy and callback delivery through the existing
Run/attempt callback authority. Keep admission fenced and roll back on any failed
or missing check. After those checks pass, admit the target:

```bash
cd "$TARGET_REPO_ROOT"
sudo -n python3 -B -m tools.s75_opensandbox_transition finalize \
  --target-repo-root "$TARGET_REPO_ROOT" \
  --target-commit "$TARGET_COMMIT" \
  --env-file "$MANAGED_ENV_FILE" \
  --docker-cmd docker
```

Before database migration or while schema compatibility still passes, an operator
may explicitly restore the captured Docker release. The rollback command repeats
quiescence, authenticates both legacy images and the executor image by immutable
Docker image ID, checks reverse schema compatibility, stops the target contour,
and restores admission only after legacy parity succeeds:

```bash
cd "$TARGET_REPO_ROOT"
sudo -n python3 -B -m tools.s75_opensandbox_transition rollback \
  --target-repo-root "$TARGET_REPO_ROOT" \
  --target-commit "$TARGET_COMMIT" \
  --legacy-repo-root "$LEGACY_REPO_ROOT" \
  --legacy-commit "$LEGACY_COMMIT" \
  --env-file "$MANAGED_ENV_FILE" \
  --legacy-backend-image "$LEGACY_BACKEND_IMAGE" \
  --legacy-frontend-image "$LEGACY_FRONTEND_IMAGE" \
  --legacy-executor-image "$LEGACY_EXECUTOR_IMAGE" \
  --docker-cmd docker
```

The rollback leaves the root-owned guard unit and isolated OpenSandbox Server
profile in place; neither exposes a host listener to sandboxes, and retaining
them keeps the next qualified migration fail-closed. Never run `migrate`,
`finalize`, or `rollback` concurrently. Never use `down -v`,
start a second Compose project, copy the managed environment file, or retry a
nonzero transition before classifying its bounded result and current runtime.

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

### Explicit backend-layer flatten recovery

If, and only if, the normal auto backend runtime rebuild stops with Docker
`max depth exceeded`, do not run `docker export`, `docker import`, `docker tag`,
or Compose by hand. Do not retag the canonical current backend subject. After a
fresh read-only readiness packet, use the normal exact-main preamble above and
replace only its authority invocation with this sole recovery command:

```bash
cd "$SOURCE"
timeout --signal=INT --kill-after=30s 27000s \
  python3 -B tools/release_authority.py deploy-main-commit \
  --release-root "$ROOT/releases" \
  --commit "$TARGET" \
  --strategy auto \
  --allow-backend-layer-flatten-recovery \
  --canonical-build-timeout-seconds 1800 \
  --docker-cmd "sudo -n docker" \
  --compose-file deploy/ai-platform/docker-compose.yml \
  --compose-file deploy/ai-platform/docker-compose.opensandbox-internal-test.yml
```

The flag is default-off. It is accepted only after the authority has completed
strict current-runtime provenance and parity, and only when the resulting
backend plan action is `runtime-rebuild`. It independently rechecks the
canonical current backend image's clean provenance and requires at least 96
RootFS layers. A lower-layer, missing, dirty, mismatched, or unsafe image fails
closed; a canonical dependency build, frontend action, promotion, or an
arbitrary build failure never activates recovery.

For that one invocation the authority creates a unique stopped container from
the verified current image without runtime environment, mounts, or Compose
data; exports it to a mode-`0600` managed temporary archive; checks its digest;
imports one unique non-canonical flat image; and rebuilds the target only from
that validated temporary base. It restores only allowlisted image configuration
(`10001:10001`, `/app`, entrypoint/CMD, required non-secret environment,
`8020/tcp`, and clean provenance labels). It verifies the flat layer count,
source markers, `0755` entrypoint, Python/Uvicorn executables, and the
`10001:10001` runtime identity before the target build. The current tag/image
and running containers remain untouched. All temporary containers, archives,
and the flat reference are removed whether export, import, validation, target
build, or final release fails. Terminal stage evidence contains only fixed
stage/action/status/timing fields, never temporary names, commands, paths,
archive contents, environment, or secrets.

After successful `compose up -d --no-build`, `deploy-main-commit --strategy auto`
uses a 45-second monotonic final-parity convergence window with a two-second
maximum poll interval. Each attempt repeats only the existing strict, read-only
parity collector; it never rebuilds an image, reruns Compose, changes a container,
or reads the managed environment file. `verify` remains a single strict parity
collection. Each collector subprocess and HTTP probe is capped at the remaining
monotonic convergence budget and uses its existing owned-process cleanup; a report
that arrives at or after the deadline cannot succeed. The convergence window retries
only transient network `OSError` or `URLError`, an unverified parity report, and the
explicit worker heartbeat startup-readiness failures. Any other authority error,
including an HTTP status error, fails closed immediately.
On convergence exhaustion, terminal evidence reports only `parity_attempts` and
the fixed `parity_last_failure_kind`; it does not include raw exception text,
URLs, endpoints, secrets, or environment data.

Backend source-only/runtime-overlay stages are bounded at 90 seconds and frontend
source-only stages at 180 seconds. Dependency-triggered canonical builds have a
separate bounded per-stage timeout: `--canonical-build-timeout-seconds` accepts
only 300 through 3600 seconds and defaults to 1800. The normal invocation
sets 1800 explicitly. This does not widen either source-only SLO. The effective
canonical timeout is recorded in the auto plan and every canonical-build stage.
On timeout the authority terminates its owned process tree and uses a short
bounded pipe-drain grace before reporting timeout, exit code when available, and
only a fixed recognized stderr category; raw command output and unrecognized
stderr remain redacted. Canonical-build timeout and nonzero-exit evidence also
includes a bounded BuildKit progress classifier. Classification happens only
after exit or owned-tree cleanup and pipe drain. It can report only the last
recognized step ordinal/total, a fixed stage kind and Dockerfile instruction
category, bounded line count, last BuildKit progress timestamp, and whether
progress advanced. Commands, URLs, package names, paths, environment/arguments,
credentials, file contents, and all other stdout text are never persisted; any
unsafe or unprovable output reports `build_progress_status: unknown`.

The exact-main authority's conservative longest-path inventory is:

- 2 default-timeout subprocess slots for coordination-source Git gates;
- 11 for existing-checkout materialization, fetch, and target verification;
- 4 for the initial managed-target gate;
- 14 for current-runtime container inspection and its full parity pass;
- 1 for runtime-diff classification;
- 22 for deploy preflights, target-image probes and verifications, sandbox-image
  revalidation, target revalidation, optional removal, and Compose convergence;
- up to 33 Compose semantic preflight parser attempts at 15 seconds each;
- 11 for the final full parity pass;
- 45 seconds for bounded post-Compose final-parity convergence retries; and
- 4 HTTP probes at 15 seconds each across current and final parity; and
- 2 sequential canonical dependency builds at 1800 seconds each.

That is 65 default subprocess slots, four HTTP slots, a bounded 45-second
convergence window, up to 33 bounded Compose semantic parser attempts, and two
canonical builds:
`65 * 300 + 4 * 15 + 45 + 33 * 15 + 2 * 1800 = 23700` seconds. The 24000-second command
deadline rounds this up with an additional 300 seconds for in-process filesystem
trust walks, scheduling, cleanup dispatch, and evidence serialization. It is a
conservative finite outer bound, separate from—and never an expansion of—any
per-operation timeout.

The explicit flatten recovery adds up to ten bounded 300-second Docker slots
(two stopped-container exports, import, validation, and cleanup) to that
inventory: `23700 + 10 * 300 = 26700` seconds. Its documented 27000-second
outer command budget leaves 300 seconds for the same in-process work. Configure
the enclosing durable runner with a 27330-second deadline for this exceptional
command only.

Use exactly `timeout --signal=INT --kill-after=30s 24000s`. `INT`, rather than
`TERM` or `KILL`, lets Python raise through the active `_run`, whose existing
`BaseException` path first terminates the authority-owned process group and
performs its bounded pipe drain. The 30-second hard-kill grace exceeds the
authority's one-second cleanup operations and remains only a stuck-cleanup
backstop. A wrapper that initially kills only the Python leader or skips this
grace is forbidden because it can orphan a mutating Docker child.

Configure the enclosing durable runner with a 24330-second deadline. It must
equal or exceed the 24000-second command budget plus the 30-second hard-kill grace
and one additional 300-second terminal-evidence margin. Do not lower either
deadline or replace a timeout with an unlimited value. Retain the compact stage
evidence and do not expose the environment file or raw command output. The
existing external lease/fencing gate remains the only overlap guard.

The local workstation does not provide Docker. The controlled-host benchmark gate must
observe backend-only auto release below 90 seconds, frontend-only below 180
seconds, and deployment-only change with zero role builds before those timings
are claimed as passed.

## Terminal Evidence

A release is complete only after the owner reports the exact commit and image,
container identity and restart counts, API/frontend health, relevant smoke,
rollback subject, authority terminal state, and final source/runtime parity.
Historical evidence or a healthy old runtime does not prove the target release.
For `deploy-main-commit`, the terminal result's non-secret `authority_commit` must
equal the requested commit; it proves the loaded coordination authority checkout
was at that exact target before immutable target materialization. If a later
authority error occurs after that gate, the CLI failure payload retains the same
`authority_commit`; a pre-authority failure must omit it.
