# Production bootstrap change contract

## Goal

Provide one repository-owned command for rebuilding the production runtime
after the operator has restored the approved host secrets and configuration:

```bash
sudo -n ./scripts/deploy-latest.sh --profile production --latest \
  --env-file /data/ai-platform-prod/config/production/.env
```

The command converges the independently managed OpenSandbox host service,
waits for the exact current `main` commit to pass the backend, frontend, and
packaging workflows, verifies the digest-bound release evidence, and deploys
the production base Compose file plus `docker-compose.opensandbox.yml`.

The internal-test profile and the existing one-time legacy-to-direct OpenSandbox
transition retain their current meanings.

## Owner and bounded change surface

The production bootstrap controller owns only host admission, OpenSandbox host
service convergence, exact-image production Compose convergence, and bounded
rollback to an already verified direct-OpenSandbox production runtime.

- `scripts/deploy-latest.sh`
- `tools/production_bootstrap.py`
- `tools/opensandbox_unit_guard.py`
- `tests/test_deploy_latest_entry.py`
- `tests/test_production_bootstrap.py`
- `deploy/opensandbox/opensandbox-production.service`
- `deploy/opensandbox/server-production.env.example`
- `deploy/opensandbox/server-production.toml.example`
- `.github/workflows/ai-platform-backend.yml`
- `tests/test_backend_ci_workflow.py`
- `README.md`
- `docs/operations/release-operations-runbook.md`
- `docs/operations/latest-main-image-quickstart.md`
- this contract

The controller reuses `tools/latest_main_quickstart.py` for exact-main Actions
and packaging admission, `tools/release_authority.py` for immutable image and
Compose authority, and the existing legacy transition controller for runtime
parity and quiescence checks. It does not create another release,
sandbox-lifecycle, model-credential, or callback authority.

## Host and secret invariants

The command runs only as root on a POSIX system. The operator must restore these
regular, non-symlink files before invoking it:

- `/data/ai-platform-prod/config/production/.env`: `root:root 0600`
- `/etc/ai-platform/opensandbox/server.env`: `root:root 0600`
- `/etc/ai-platform/opensandbox/server.toml`:
  `root:<OPENSANDBOX_SERVER_GID> 0640`

The TOML's group must equal the dedicated server GID declared in `server.env`.
That single group-read bit is required because the unit runs the server as the
dedicated non-root identity; no other secret file is group-readable.

The controller never prints or copies their contents and never generates a
production API key, model credential, callback key, or proxy token. It may parse
only the bounded fields required to prove host identity and OpenSandbox safety.
Python 3.11 or newer, Docker with Compose v2, systemd, and a registered `runsc`
runtime are base-host prerequisites; absence fails before application Compose
mutation.

The OpenSandbox environment must bind distinct private lifecycle and egress
addresses, use a digest-bound server image, identify a dedicated non-root
UID/GID, and match the Docker socket group. The server digest must be verified
out of band against an approved `server/v0.1.13` or newer release; upstream OCI
labels do not prove that source version. The TOML must use Docker bridge mode,
set `docker.host_ip` to the lifecycle address, select gVisor `runsc`, pin both
execd and the egress sidecar by digest, use `dns+nft`, disable IPv6 egress, deny
all host bind mounts, and contain no global sandbox binds or environment
injection. The lifecycle API key is restricted to 32-256 plain URL-safe ASCII
characters so both TOML and Compose parse the same secret. The controller
creates only the named lifecycle network plus canonical server-state and
platform-workspace directories, installs the exact checkout's reviewed unit as
`opensandbox.service`, pulls all three immutable host images, and validates the
running service without exposing configuration values.

The installed unit records a SHA-256 fingerprint of the complete parsed host
environment and TOML contract. An update of an existing application runtime must
match that fingerprint exactly. Server image, API key, execd/egress image,
UID/GID, address, or policy changes require a separately reviewed
host-maintenance contract; they cannot silently enter the one-command application
rollback path.

Before host mutation, the controller also proves the application env uses the
same plain, unquoted lifecycle URL, lifecycle API key, and egress bind address
as the host files. Both private addresses must be assigned to the production
host. The application executor remains the admitted backend workload image; it
is separate from the host TOML's digest-bound OpenSandbox `runtime.execd_image`.
This prevents a syntactically valid Compose deployment from starting against a
different OpenSandbox trust boundary without conflating the two image roles.

The OpenSandbox server is a trusted host control-plane component. Its Docker
socket bind is read-only at the filesystem layer, but Docker API access still
grants effective Docker daemon authority; the unit hardening is defense in depth,
not a host-isolation boundary. Production therefore admits only the root-owned,
repository-managed unit and exact runtime contour, and still requires the real
runsc and sandbox-lifecycle acceptance described below. The trusted server
container itself uses Docker's host control-plane runtime; `runsc` is required
for the executor/sandbox containers it creates and is proved on those containers.
Before start and stop, the unit's isolated guard inspects the fixed container name
and removes it only when the immutable image reference and all repository
ownership labels match. A foreign same-name container blocks the service
operation.

## Release and runtime invariants

- The candidate is the exact 40-character SHA at the fixed repository's
  `refs/heads/main`, with successful required final jobs for backend, frontend,
  and packaging and a verified ready artifact for the same run attempts.
- Backend and frontend are admitted only as role-bound GHCR digest references
  whose release labels and local image identities pass release-authority checks.
- The target checkout is owner-managed, clean, exact-SHA, and materialized under
  `/data/ai-platform-prod/releases/<commit>`.
- Production uses Compose project `ai-platform-internal`, the base Compose file,
  and `docker-compose.opensandbox.yml`, with
  `SANDBOX_WORKSPACE_ROOT=/data/ai-platform-prod/runtime-workspaces`.
- A cold bootstrap requires zero containers in that Compose project. A partial,
  legacy, foreign, or ambiguous project blocks before mutation.
- An update of an existing direct-OpenSandbox production runtime requires exact
  current parity, zero nonterminal Runs/RunAttempts/leases and zero managed
  sandbox containers, schema equality sufficient for image rollback, and an
  already installed bootstrap-managed OpenSandbox unit with the same admitted
  host-configuration fingerprint. The command does not adopt a foreign or
  manually managed host service, or change OpenSandbox host configuration, during
  an application update.
- One owner-managed lock covers candidate discovery, host convergence, image
  preparation, Compose mutation, health/parity checks, and rollback.
- GitHub credentials are removed before Docker, systemd, Compose, and health
  commands execute.

## Failure, rollback, and stop conditions

Failure before Compose mutation leaves the application runtime unchanged. For
an existing verified direct-OpenSandbox runtime, target deployment or parity
failure stops every available target admission container, proves quiescence
again, and then performs one bounded restore from the exact previous checkout
and local immutable images. If that rollback fence cannot be proved, the
controller leaves admission stopped and does not start the previous image set.
A cold bootstrap has no previous application runtime to restore. A failed first
start fences admission and removes only the newly created Compose containers and
networks with `down --remove-orphans`; named data volumes remain in place so the
same approved subject can be retried. If that cleanup cannot be proved, the
command fails closed for operator inspection. Database migrations are never
reversed.

The installed OpenSandbox unit has its own reviewed-template provenance. If an
update changed that template, rollback restores and revalidates the previous
unit before starting the previous application images. Container validation
always requires the exact source label embedded in the installed unit, the
digest-selected image ID and entrypoint, and the expected host configuration.

The command stops without mutation for missing base-host prerequisites, unsafe
configuration metadata, placeholder or inconsistent OpenSandbox configuration,
untrusted unit ownership, active work, schema drift, incomplete runtime
membership, failed Actions or artifact evidence, mutable/mismatched images, or
failed Compose semantic preflight. Scope expansion into OS package installation,
secret-manager integration, DNS/TLS provisioning, or automated product-level
acceptance requires a revised contract.

## Falsifiable acceptance and evidence ceiling

Focused tests must prove configuration validation, placeholder rejection,
private-address separation, runsc admission, idempotent lifecycle-network and
unit convergence, token isolation, cold-runtime membership gating,
exact-production Compose selection, existing-runtime quiescence, deployment
ordering, and one-attempt rollback. Shell syntax, Python compilation, formatting,
and the focused CI shard must pass.

Local source tests and mocked command sequencing prove only the controller
contract. A real production run must separately record the exact source and image
digests, OpenSandbox service/container identity, production Compose parity,
API readiness, one application-owned create/execute/file-collect/delete cycle,
`HostConfig.Runtime=runsc`, network denial/egress reachability, callback/model
proxy behavior, and orphan-free cleanup before production acceptance is claimed.
