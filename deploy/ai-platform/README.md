# Deploy a released version

This guide applies only to previously published production Releases. The current
publication workflow emits only the internal-test bridge package; its own
`README.md` is included in that archive. Do not use that package on a production
host or reconstruct a production package from these source templates.

Download `ai-platform-production.tar.gz`, the single operator asset,
from the **chosen immutable Deployment Release** on the official repository.
The manifest, verified release evidence and `BACKUP-RESTORE.md` are inside it.
GitHub may also display its
automatically generated source archives; those are not deployment packages.
Do not mix files from different versions or use an untrusted archive: the package
contains executable deployment code. Image digests and the application commit
are already fixed in the package. Git, Actions access, a source checkout and
host-side image builds are not required.

The packaged environment example contains operator configuration. Application
image references, source commit and executor image digest are bound by the
Release and omitted from that example.

The package selects OpenSandbox, gVisor and the server proxy. Workspace paths and
physical network topology are operator configuration. New leases use the same
signed identity and public-egress path in functional tests and deployment.

OpenSandbox configuration has two owners:

| Configuration | Owner |
| --- | --- |
| Lifecycle connection | Application env: `OPENSANDBOX_BASE_URL` and `OPENSANDBOX_API_KEY`. |
| Workspace and capabilities | Application env: `SANDBOX_WORKSPACE_ROOT`, existing `SANDBOX_WORKSPACE_MIGRATION_SOURCE`, `SANDBOX_CALLBACK_TOKEN`, `MODEL_PROXY_INTERNAL_TOKEN` and `SANDBOX_EGRESS_PROOF_SIGNING_KEY`. The host TOML allowlist contains exactly the configured workspace root. |
| Network topology | Application env: `OPENSANDBOX_EXPECTED_NETWORK_MODE`, `OPENSANDBOX_EGRESS_BRIDGE`, `OPENSANDBOX_EGRESS_SUBNET` and `OPENSANDBOX_EGRESS_PROXY_IPV4`. Match the network name in host TOML and the other three values in protected `server.env`. |
| Model connection custody | `MODEL_CONNECTION_ENCRYPTION_KEY` in API and Worker plus `MODEL_CONNECTION_ALLOWED_INTERNAL_HOSTS` when the configured origin is internal. The upstream origin, credential, directory and capacities remain Models-page owned. |
| Timeouts | Optional application tuning: `OPENSANDBOX_REQUEST_TIMEOUT_SECONDS`, `OPENSANDBOX_TIMEOUT_SECONDS`. |
| Kernel isolation and host firewall | Host OpenSandbox TOML, Docker `runsc` runtime and network-guard service, prepared once by the host administrator. |

Workspace files live in one Docker-root-external host directory shared by API,
Worker and OpenSandbox. OpenSandbox receives only the current authoritative
Attempt workspace as a read-write Host volume; it never receives the workspace
root or a user-supplied host path. The file API remains limited to the exact
lease sentinel used to prove that both sides see the same mount. Network egress
allows public Internet access under the host network policy. The network name,
Linux bridge, RFC1918 IPv4 subnet and proxy address can be selected for this host;
the proxy must be a usable address in the subnet, distinct from its gateway.
Changing an existing network requires a drained maintenance window and matching
host rules. The deployment environment remains production; build commit and dirty
markers are supplied during image construction.

`AI_PLATFORM_API_UPSTREAM` selects the platform API reached by the frontend proxy.
The executor uses the platform proxy on the dedicated OpenSandbox network. Its
internal alias and port are supplied by Compose. Configure the upstream URL,
write-only key, enabled models and
capacities in the administrator's Models page before running a model. Environment
provider URLs and credentials are omitted from released packages and cannot
substitute for the Run's pinned database connection revision.

The browser's retained `/settings` address redirects to `/models`. The removed
generic settings API did not persist or apply submitted values; model changes
now have one browser entrance and one database-backed control plane.

`OPENAI_MODEL`, `ANTHROPIC_MODEL`, `CLAUDE_AGENT_MODEL`, `DEFAULT_MODEL_ID`
and `MODEL_CATALOG_JSON` supply legacy model selection before the database
catalog is active. That selection can create a Run without a connection revision;
it does not provide a working production proxy fallback. Model gateway request
concurrency is currently unbounded by the platform; the capacity report records
enforcement as unimplemented.

## Prepare the host once

Use a Linux host with Python 3, Docker, Docker Compose supporting `--wait` and
`!reset`, and a configured, active `opensandbox.service`. OpenSandbox host provisioning (credentials, network policy, the exact
configured workspace-root Host-volume allowlist and workspace permissions) is
a separate first-install prerequisite, not repeated during application upgrades.
The host guard reads its bridge, subnet and proxy address from protected
`server.env`; host/application validation checks that these values agree.

Extract the chosen archive into a new directory. Keep this directory unchanged.
For an existing installation, use its existing environment file and the same
Docker host. **Do not copy the example over an existing environment file.**
For a new installation:

```sh
umask 077
cp .env.example .env
# Edit .env: replace example secrets, set the public origin, authentication,
# model encryption and OpenSandbox connection settings for this host.
chmod 600 .env
```

The configuration must be owned by the invoking user. If that user requires
sudo for Docker, use `--docker-cmd 'sudo -n docker'`; do not run the entire
entry as another user against a differently owned configuration.

For ProfileDrive HTTPS file imports with a private CA, set
`PROFILE_DRIVE_TRANSFER_CA_CERT_HOST_FILE` to an absolute, readable host path
containing only the trusted public CA certificate and set
`PROFILE_DRIVE_TRANSFER_CA_CERT_FILE` to its target path inside the API container.
Only when that target is configured does deployment apply the package's CA
Compose overlay. Deployment preflight rejects missing, linked, malformed,
group/world-writable or private-key-bearing host files and checks the CA from a
network-disabled backend container running as the API user. Compose binds the
file read-only and never creates a missing host path. Never mount the TLS
private key. Verify the certificate matches the configured HTTPS upstream and
has not expired. The mount survives application container recreation as long
as the host certificate and Compose configuration remain in place. Plan
certificate renewal before expiry; after replacing a certificate file,
recreate the API container through the authorized release procedure so its bind
mount uses the new file.

Production requires independent generated `TRUSTED_PRINCIPAL_SECRET` and
`AI_SESSION_SECRET` values of at least 32 characters; blank values and known
placeholder prefixes are rejected before application services stop. Preserve
these secrets across upgrades. Keep `AI_SESSION_COOKIE_SECURE=true` and
`AUTH_CONTEXT_COOKIE_SECURE=true`, with the browser-visible HTTPS origin in
`CORS_ALLOW_ORIGINS`.

An intentionally HTTP-only, trusted isolated intranet needs both secure-cookie
flags set to `false`, its actual HTTP browser origin, and the explicit
`--allow-insecure-http` flag on each deployment or preflight command. This prints
a warning and does not configure TLS or a firewall. HTTP exposes session and
gateway traffic: restrict network access, firewall direct API access, and prefer
TLS. Preserve the same generated secrets.

## Install or upgrade

Back up the database, object storage, Redis, and workspace files as a coordinated
recovery set before upgrading (see the backup checklist below). Choose a maintenance window with no
active tasks or sandbox leases. From the extracted directory:

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
host path containing the existing data, back it up and add
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
not an automatic API/Worker startup action. After a verified recovery set,
finish/cancel old Runs and block admission. Set `CUTOVER_BEFORE` to the approved,
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

## Backup checklist

Use the packaged [backup and restore procedure](BACKUP-RESTORE.md) for concrete
PostgreSQL custom dumps, cold Redis/MinIO volume backups, host workspace backups,
protected keys and an isolated restore rehearsal.

Before an upgrade, record the current immutable package/commit, persistent
container and volume identities, workspace paths, and schema version. Protect
the operator environment file and encryption/signing keys in an encrypted,
access-controlled backup, separate from public deployment evidence. A database
dump alone does not cover MinIO objects or workspace files.

Block new admission, let active work finish, and verify no active Runs, Attempts,
leases, or sandbox containers before stopping API, Worker and frontend for the
backup window. Keep admission stopped while taking the coordinated set:

- A PostgreSQL custom-format dump, checked with `pg_restore --list` and an
  isolated restore rehearsal
- MinIO objects and metadata, Redis persistent data, and the current workspace
  root, preserving permissions and ownership; use an approved snapshot/export
  method (raw filesystem copies require the corresponding service stopped)
- Any retained legacy workspace source, release package/manifest, and protected
  configuration needed to interpret and decrypt the restored data

Record checksums, completion times and restore-test results. Verify all backup
parts before proceeding. Stopping persistent services for a cold backup is a
separate maintenance action; restart and verify the same containers before the
package upgrade, which otherwise preserves their identity and restart counts.

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
understand a schema that has advanced. For an authorized restore, first preserve
the failed state, rehearse restoration in an isolated environment, and select
the package compatible with the backup's schema. Restore the matching database,
object storage, Redis and workspaces from the same recovery point with all
writers stopped; retain required encryption keys and original permissions.
Never overwrite live data or reconnect a restored database to mismatched newer
objects/workspaces. Do not edit migration checksums. Validate restored state,
image identity, API readiness, advancing Worker heartbeat, OpenSandbox isolation
and quiescence before reopening admission. Record any explicitly accepted data
loss since the recovery point.

The package does not fetch `main`, change the Docker daemon proxy, provision the
OpenSandbox host, remove historical release directories, or clean data volumes.
Its running container/image identity is the deployment authority; legacy
source-checkout `subject` files are not updated or used by this entry.
