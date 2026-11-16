# Deploy a released version

There is one deployment package: `ai-platform-internal-test.tar.gz`. Download it
from the chosen immutable official Deployment Release. `internal-test` in the
archive name and this source filename is a retained historical name, not a
choice between two deployment packages. The assembler includes this guide as
`README.md` inside the archive; use it for both first installs and upgrades.

Its `deploy.py`, `compose.yaml`, `compose.override.yaml`, pinned image manifest
and release evidence belong to the same commit. Do not repack the archive or
mix files from different Releases. Git, a source checkout and host image builds
are not required. Keep the existing configuration, Compose project, volumes and
workspace identity; the retained `/data/ai-platform-internal-test` directory
name is not a reason to move an existing installation.

## Host and configuration prerequisites

Use Linux, Python 3, Docker, Compose v2 supporting `--wait` and `!reset`, and an
already-provisioned, active `opensandbox.service`. Host provisioning and changes
to lifecycle credentials, workspace permissions or network policy are separate
controlled operations, not application upgrade steps.

The current Compose overlay still fixes `DEPLOYMENT_ENVIRONMENT=test` and
`SANDBOX_SECURITY_PROFILE=internal-test`, and selects the native OpenSandbox
server proxy. These are enforced configuration values, not package variants.
The host OpenSandbox server must use
`network_mode = "bridge"` and `docker_runtime = "runsc"`; do not enable its
NAT-redirect egress sidecar (incompatible with gVisor). The bridge does **not**
provide the production host firewall guard: sandbox processes may reach network-
accessible public, private, metadata and host addresses. Use only on a trusted,
isolated test host with appropriate external network controls. This package
does not support `DEPLOYMENT_ENVIRONMENT=production` or establish
production-equivalent network isolation. A single package does not make its
network controls equivalent to the retained dedicated-network host templates.

The OpenSandbox API URL/key, server proxy, image digest, workspace host bind,
Run/Attempt identity, callback token and model proxy capability remain checked.
Set `OPENSANDBOX_DOMAIN` and `OPENSANDBOX_PROTOCOL` for the private lifecycle
server (or a valid `OPENSANDBOX_BASE_URL`). Its address must be a private
non-loopback IPv4 literal reachable from the API and Worker containers.
Set `SANDBOX_CALLBACK_BASE_URL` to
`http://<bridge-gateway>:8020` with the API published on host port 8020;
`api.sandbox.internal` alias is not reachable from ordinary Docker `bridge`.
Set `OPENSANDBOX_EGRESS_PROXY_URL` to the host endpoint reachable from the
ordinary OpenSandbox bridge. Bind the proxy's port 18043
using `OPENSANDBOX_EGRESS_PROXY_BIND_ADDRESS` on the Docker `bridge`
gateway address. The package controller compares that host bind with the
actual bridge gateway and the API/Worker `OPENSANDBOX_EGRESS_PROXY_URL`
(`http://<bridge-gateway>:18043`); do not publish it on 0.0.0.0.
`SANDBOX_RUNTIME_SUBJECT` and `SANDBOX_WORKSPACE_ROOT` must match the existing
installation. Both API and Worker need the same model encryption and
callback credentials; the existing `SANDBOX_CALLBACK_TOKEN` must contain at
least 32 characters because it also derives executor control
authentication. Preserve it across restarts. Model connection contents remain
administrator-owned and are not stored in this package.

For private-CA ProfileDrive HTTPS imports, retain the existing public CA file
and set both `PROFILE_DRIVE_TRANSFER_CA_CERT_HOST_FILE` (absolute, readable host
file) and `PROFILE_DRIVE_TRANSFER_CA_CERT_FILE` (API container path). The
optional packaged CA overlay mounts only that certificate read-only. Preflight
rejects missing, linked, malformed, group/world-writable and private-key files
and verifies the mount as the API user. Never mount a private key or replace the
existing certificate without its approved renewal procedure. Verify that it
matches the configured HTTPS upstream and has not expired. Plan renewal before
expiry; after approved file replacement, recreate the API container through
the authorized release procedure so the bind mount uses the new certificate.

This configured profile may instead reach the in-network connector over plain HTTP by
setting `PROFILE_DRIVE_TRANSFER_UPSTREAM=http://profile-drive-mcp:5201` together
with `PROFILE_DRIVE_TRANSFER_ALLOW_INSECURE_HTTP=true`; leave the CA paths empty
then. That transfer sends the invoking user's company JWT and the file bytes
unencrypted, so configure the API to use the Docker-network service address,
not an endpoint reached through a host-published port. Preflight rejects an HTTP upstream unless that
flag is true, and the API rejects it while the flag is false. `--allow-insecure-http`
below covers only the browser origin and cookies; it does not enable this path.

### ProfileDrive transfer budgets

ProfileDrive transfers now have finite configurable budgets. The API applies
`PROFILE_DRIVE_TRANSFER_READ_TIMEOUT_SECONDS` (default 60, positive and at most
3600) to network idle waits and `PROFILE_DRIVE_TRANSFER_TOTAL_TIMEOUT_SECONDS`
(default 1800, positive and at most 10800) as one deadline starting when the
transfer owner is acquired, including connection, response headers, body and
claim/admission waits and ASGI downstream sends. Each body read uses the smaller remaining
budget; continuous small chunks cannot reset the total deadline. This is new
behavior: stalled upstreams no longer hold requests indefinitely. The 30-minute
default leaves roughly 291 KiB/s for the existing 512 MiB import limit; operators
must tune it to their upstream bandwidth/SLA rather than disable the bound.
The independent 3-hour upload reservation expiry and 300-second object-storage
put budget are unchanged. These are server operation budgets, not time that a
user spends selecting a file before acquisition.

The Files transfer owner closes both HTTPX resources on admission rejection,
request cancellation and downstream disconnect, including failure before body
iteration. Each close has its own shielded 5-second cleanup budget; failures are
logged without upstream exception text, paths or credentials and do not replace
the primary error. Transfer timeout reports `profile_drive_transfer_timeout`
(503 before response headers); once a runtime callback stream starts, an error
terminates the stream rather than claiming complete content. Existing denial,
size-limit and invalid-content errors retain their meanings.

## Model configuration

`AI_PLATFORM_API_UPSTREAM` selects the platform API reached by the frontend
proxy. Configure the model upstream URL, write-only key, enabled models and
capacities in the administrator's Models page before running a model.
`MODEL_CONNECTION_ENCRYPTION_KEY` must match in API and Worker; configure
`MODEL_CONNECTION_ALLOWED_INTERNAL_HOSTS` when the model origin is internal.
Provider URLs and credentials are omitted from released packages and cannot
substitute for the Run's pinned database connection revision.

The browser's retained `/settings` address redirects to `/models`. The removed
generic settings API did not persist or apply submitted values; model changes
now have one browser entrance and one database-backed control plane.

`OPENAI_MODEL`, `ANTHROPIC_MODEL` and `CLAUDE_AGENT_MODEL` remain SDK/adapter
defaults for direct or legacy executor use. They do not publish a public catalog
or admit a new Run without a database connection revision and paired token
capacities. `DEFAULT_MODEL_ID` and `MODEL_CATALOG_JSON` are retired deployment
settings; remove them from existing operator configuration. Public default model
selection comes only from the database-backed Models control plane. Model gateway
request concurrency is currently unbounded by the platform; the capacity report
records enforcement as unimplemented.

## Prepare the environment file

For an upgrade, keep the existing owner-held `.env` (mode `0600`), data volumes,
PostgreSQL, Redis, MinIO and workspace directory. Do not copy `.env.example`
over it, create replacement secrets, or remove legacy workspaces.
For a new installation only:

```sh
umask 077
cp .env.example .env
# Configure this host's origin, credentials, storage and OpenSandbox endpoints.
chmod 600 .env
```

The invoking user must own this file. When Docker needs sudo, add
`--docker-cmd 'sudo -n docker'`; do not run the entire entry as a different user.
Never print environment values or put them in Git, issue text or release evidence.

Use independent generated `TRUSTED_PRINCIPAL_SECRET` and `AI_SESSION_SECRET`
values of at least 32 characters. Preserve these and the model encryption and
callback credentials across upgrades. Prefer HTTPS with both
`AI_SESSION_COOKIE_SECURE=true` and `AUTH_CONTEXT_COOKIE_SECURE=true` and the
browser-visible origin in `CORS_ALLOW_ORIGINS`. Only for an intentionally HTTP-only
trusted isolated intranet, set both cookie flags false and pass
`--allow-insecure-http` on each deployment/preflight command. This acknowledges
browser-origin/cookie exposure; it does not enable ProfileDrive HTTP transfers
or provide TLS or firewall protection.

## Install or upgrade

Back up recoverable data before migration; the controller creates no backup.
Choose a maintenance window with no active Runs, Attempts, leases or sandbox
containers. From the extracted directory:

```sh
python3 deploy.py --env-file /absolute/path/to/.env
```

This checks configuration, downloads images, verifies locally available digest
identities, checks activity, stops application admission, checks activity again,
runs schema migration and workspace initialization, starts the selected
application, and verifies API readiness, container identity, OpenSandbox
reachability and an advancing Worker heartbeat. Data volumes are never deleted.

A fresh installation uses `SANDBOX_WORKSPACE_ROOT` directly. An
absent legacy source skips `workspace-migrate`; do not create a dummy
legacy directory. Any existing legacy source directory, even empty, requires
explicit migration. Set `SANDBOX_WORKSPACE_MIGRATION_SOURCE` to the inspected
host path containing the existing data, then add
`--migrate-legacy-workspaces`. The migration mounts the source read-only, verifies
path/type/mode/owner/size and SHA-256 inventory, and retains the source. Existing
current-layout bind mounts with no legacy source need no migration. A retained
legacy source still requires the explicit flag on later invocations. Existing
binds and plain local named volumes can be copied only when both API and Worker inspect
records identify that exact source and storage identity. For a local named
volume, use its inspected host mountpoint as the read-only migration source.
Volume inspection must confirm the `local` driver, matching mountpoint and no
mount options. Bind-backed local volumes, NFS and volume plugins require a
separately classified migration because their data can disappear from the host
mountpoint when the last container stops.
Unsupported layouts require operator-classified migration; never point an upgrade
at an empty root to bypass that check.

To check configuration, activity and already-cached images without downloading
or changing application/data services, add `--check`. It may run a temporary,
network-disabled, read-only verifier container from the digest-verified backend
to check a root-owned workspace for an incomplete migration marker; it makes no
application/data changes. This is preflight only, not deployment
acceptance. Expired quarantined records are not automatically deleted: only
terminal, unclaimed failed-reconciliation records tied to a terminal Run may
be excluded, and any surviving recorded sandbox container still blocks.

Success ends with `deployment: healthy (<commit>)`. A returned nonzero status is
not a successful deployment. Do not start another invocation while one is still
running; the project-wide lock prevents concurrent package deployments.

## Legacy SSE upgrades

An installation using the retired SSE transport requires explicit legacy-state
retirement before schema migration. This is an authorized one-time data change,
not an automatic API/Worker startup action. Finish/cancel old Runs and block
admission. Set `CUTOVER_BEFORE` to the approved,
timezone-qualified UTC cutover timestamp. With the new package's images already
verified, use its exact Compose project, env file and Docker command:

```bash
SSE_COMPOSE=(docker compose --project-name ai-platform-internal \
  --env-file /absolute/path/to/.env -f compose.yaml -f compose.override.yaml)
"${SSE_COMPOSE[@]}" stop frontend api worker
"${SSE_COMPOSE[@]}" run --rm --no-deps --pull never --entrypoint python migrate \
  /app/tools/retire_legacy_sse_streams.py --before "$CUTOVER_BEFORE"
# Review the private inventory, then apply the explicitly approved retirement.
"${SSE_COMPOSE[@]}" run --rm --no-deps --pull never --entrypoint python migrate \
  /app/tools/retire_legacy_sse_streams.py --before "$CUTOVER_BEFORE" --apply
```

Use `sudo -n docker` in the array when required. Keep producers stopped until the
normal package migration completes. Apply refuses selected nonterminal Runs.
`applied: true` confirms commit; `applied: null` / `legacy_sse_commit_uncertain`
requires a fresh inventory before deciding whether to repeat. Retirement keeps
Run/Attempt facts, final results, receipts and audit history; Redis keys expire
without deletion. The schema guard rejects unretired or partially present legacy
state. After schema `2026.09.12.1`, older backend images are incompatible. Recovery
requires a compatible corrected package or an explicitly authorized coordinated
restore; there is no speculative binary/database rollback.

## Already downloaded or offline images

Load images into the same Docker daemon, retaining the package's complete
`repository@sha256:...` identities, then run:

```sh
python3 deploy.py --env-file /absolute/path/to/.env --offline
```

This skips downloads, not local image verification. Missing images or RepoDigests
stop before admission changes. Never manually tag an image to pretend that it
has the required digest. No temporary script edits or Git bundles are needed.

## Failure and recovery

Classify the state before retrying. Do not run `down -v`, prune volumes, delete
workspace migration markers, fabricate an install journal, or switch Compose
project names to make a failed installation appear fresh.

- Configuration, download or image-verification failure before first mutation:
  existing services stay untouched; fix the prerequisite and retry normally.
- Interrupted first installation with an intact journal and **no application
  containers, including stopped ones**: the narrow resume path below may apply.
- Any application containers after a failed first install, a missing/changed
  journal, changed configuration/package, orphaned data volumes, foreign
  container ownership, or partially-created activity tables: stop and use
  operator recovery. These are not automatically resumable or fresh installs.
- Activity appears after the first check: no migration begins; stopped original
  application containers are restarted.
- Workspace migration failure: admission remains stopped. Preserve the original
  source and incomplete marker. A supported migration can continue only when
  every existing target entry matches the source; a missing source must not be
  treated as an empty fresh install.
- Schema migration or later startup failure: data is retained and application
  admission is stopped. Inspect the failing service through privileged operations;
  raw logs and resolved Compose configuration may expose secrets.

### Narrow first-install resume

Before the first service mutation, the controller writes the owner-held mode
`0600` `.ai-platform-install-state.json` beside the operator env file. It retains
that journal after failure and removes it after successful installation. The
journal binds the source commit, complete rendered configuration (as a hash),
and selected workspace migration mode. Even correcting a configuration value
invalidates automatic resume. Keep the original package, configuration and
migration selection unchanged.

For a same-input transient failure with only data containers (or none), run:

```sh
python3 deploy.py --env-file /absolute/path/to/.env --resume-install
```

Include the same applicable `--migrate-legacy-workspaces`,
`--allow-insecure-http`, Docker command and offline options. Existing data image
IDs and full persistent volume identities must match the package. Resume does
not reset or recreate persistent data. It checks database activity and sandbox
inventory; a partial activity schema requires operator recovery. A resume
preflight using `--resume-install --check` requires the existing PostgreSQL
container already running and all images already cached; it never starts it.

### Operator recovery and restore

Keep admission blocked. Capture a redacted inventory and identify the last
completed migration, failed one-shot/startup step, package/configuration, data
identities and available recovery set. Fix a same-schema infrastructure problem
or select a proven schema-compatible corrected release. A changed configuration
or first-install journal is not a license to delete that journal and retry:
classify and approve the exact recovery operation first.

There is **no automatic image or database rollback**. A prior binary may not
understand a schema that has advanced. For an operator-authorized restore,
first preserve the failed state and select a package compatible with the
recovery point's schema. Restore matching database, object storage, Redis and
workspaces with all writers stopped; retain required encryption keys and original
permissions. Never overwrite live data or reconnect a restored database to
mismatched newer objects/workspaces. Do not edit migration checksums. Validate
restored state, image identity, API readiness, advancing Worker heartbeat,
OpenSandbox isolation and quiescence before reopening admission. Record any
explicitly accepted data loss since the recovery point.

The package does not fetch `main`, change the Docker daemon proxy, provision the
OpenSandbox host, remove historical release directories, or clean data volumes.
Its running container/image identity is the deployment authority; legacy
source-checkout `subject` files are not updated or used by this entry.

## Independent acceptance

Verify the target commit/digests, retained data-service identities, workspace
bind, real `runsc` task, callback, network behavior and persisted-lease cleanup
before retiring the previous Release. A healthy controller result alone is not
Sandbox acceptance. Tenant-wide admin orphan cleanup does not delete untracked
OpenSandbox containers; classify unknown stopped containers separately without
broadening deletion scope. The repository release operations runbook owns the
release procedure; this packaged guide owns the install/upgrade command details.
