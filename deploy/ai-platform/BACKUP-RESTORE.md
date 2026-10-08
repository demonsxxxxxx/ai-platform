# Backup, restore, and classified recovery

This is an operator procedure, not an automatic rollback or permission to deploy.
Commands below are templates to review for the selected host and recovery point.
They have not been executed as a production backup or restore rehearsal by this
change. Obtain maintenance and restore authority before changing services or data.
Never run `down -v`, prune volumes, delete original workspaces, discard an install
journal, or erase an incomplete-migration marker to bypass a deployment gate.

## Recovery set and prerequisites

A consistent recovery set includes PostgreSQL, Redis persistence, MinIO objects
**and metadata**, current host workspaces, any retained legacy workspace source,
and the matching immutable package and image versions. PostgreSQL alone is not
sufficient. Stop all writers for the entire set, including API, Worker, sandboxes,
external object-storage clients, and any independent maintenance jobs.

Preserve the operator env file and host OpenSandbox configuration in an encrypted,
access-controlled backup. Retain `MODEL_CONNECTION_ENCRYPTION_KEY`, every still-used
key and key ID in `MCP_ENCRYPTION_KEYS_JSON`, `MCP_ENCRYPTION_CURRENT_KEY_ID`,
`TRUSTED_PRINCIPAL_SECRET`, `AI_SESSION_SECRET`, company JWT material, database and
MinIO credentials, OpenSandbox/callback/proxy tokens, and current/previous egress
proof signing keys. Do not generate replacements during recovery: old encrypted
records require their original keys. Never print environment files, full Docker
inspect output, resolved Compose configuration, or credentials into evidence.

The examples use Bash, GNU tar with ACL/xattr support, `age` with an approved
backup public recipient, and the authorized local Linux Docker daemon. The backup
and restore staging locations must be encrypted, access-controlled filesystems.
Use the approved storage snapshot/export procedure instead for non-local volume
drivers, remote Docker daemons, or filesystems that cannot preserve metadata.
Check available space, backup retention, key recovery, and image availability first.

## 1. Quiesce and inventory

Use the candidate immutable package with all required images already cached and
verified. Keep the currently running release package separately for the recovery
set. Do not source the env file as shell code. Run these commands only in a
maintenance window with admission blocked at the ingress and all active work
finished or explicitly cancelled through its normal authority.

```bash
set -euo pipefail
umask 077
ENV_FILE=/absolute/path/to/operator.env
RUNNING_ARCHIVE=/absolute/path/to/currently-running/ai-platform-internal-test.tar.gz
BACKUP_ROOT=/mnt/encrypted-backups/ai-platform
BACKUP="$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)"
DOCKER=(docker) # Or: DOCKER=(sudo -n docker)
# Set BACKUP_RECIPIENT to your approved age public recipient, never a private key.
: "${BACKUP_RECIPIENT:?set the approved backup public recipient}"
mkdir -p "$BACKUP_ROOT"
mkdir -m 700 "$BACKUP"
CHECK_ARGS=(--env-file "$ENV_FILE" --offline --check)
# Add --migrate-legacy-workspaces and/or --allow-insecure-http when applicable.
python3 deploy.py "${CHECK_ARGS[@]}" --docker-cmd "${DOCKER[*]}"
"${DOCKER[@]}" stop --time 30 ai-platform-frontend ai-platform-api ai-platform-worker
python3 deploy.py "${CHECK_ARGS[@]}" --docker-cmd "${DOCKER[*]}"

for service in postgres redis minio api worker frontend; do
  "${DOCKER[@]}" inspect --format \
    '{{.Name}} {{.Id}} {{.Image}} {{.Config.Image}} {{.RestartCount}} {{json .Mounts}}' \
    "ai-platform-$service"
  image_id=$("${DOCKER[@]}" inspect --format '{{.Image}}' "ai-platform-$service")
  "${DOCKER[@]}" image inspect --format '{{.Id}} {{json .RepoDigests}}' "$image_id"
done > "$BACKUP/runtime-inventory.txt"
"${DOCKER[@]}" exec ai-platform-postgres sh -ceu \
  'psql -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select version, checksum_sha256 from schema_migrations order by version"' \
  > "$BACKUP/schema-versions.txt"
"${DOCKER[@]}" exec ai-platform-postgres postgres --version > "$BACKUP/postgres-version.txt"
"${DOCKER[@]}" exec ai-platform-redis redis-server --version > "$BACKUP/redis-version.txt"
"${DOCKER[@]}" exec ai-platform-minio minio --version > "$BACKUP/minio-version.txt"
```

Stop on any failed check. The preflight is not a lock against an independent
operator or external writer; keep the maintenance window exclusive. Record the
UTC recovery point, current application commit, image digests/IDs, OpenSandbox
versions/policy, actual workspace paths, and release archive checksum. Preserve
or arrange approved offline access to every recorded image digest; a tag is not
a version guarantee. Application and data image identities are both required.
An ordinary upgrade preserves data containers, so their actual running image
digests may differ from the candidate package's data pins. Restore cold volumes
with the recorded **actual running data-image digests**, never by substituting
the newest package's PostgreSQL, Redis or MinIO images.

## 2. Capture the coordinated set

PostgreSQL stays running only while creating the logical dump. `pg_dump` includes
the application's schema and data, but not cluster-wide roles or tablespaces.
For a nonstandard cluster, separately capture its required globals using the
approved secret-safe database procedure; do not print role password hashes.

```bash
"${DOCKER[@]}" exec ai-platform-postgres sh -ceu \
  'pg_dump --format=custom --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  > "$BACKUP/postgres.dump"
# Structural readability is necessary, but is not a restore rehearsal.
"${DOCKER[@]}" exec -i ai-platform-postgres pg_restore --list \
  < "$BACKUP/postgres.dump" > "$BACKUP/postgres-toc.txt"

# A clean stop flushes Redis's multipart AOF and closes MinIO's data files.
"${DOCKER[@]}" stop --time 60 ai-platform-postgres ai-platform-redis ai-platform-minio
for service in postgres redis minio; do
  test "$("${DOCKER[@]}" inspect --format '{{.State.Running}}' "ai-platform-$service")" = false
  target=/data
  test "$service" != postgres || target=/var/lib/postgresql/data
  volume=$("${DOCKER[@]}" inspect --format \
    "{{range .Mounts}}{{if eq .Destination \"$target\"}}{{if eq .Type \"volume\"}}{{.Name}}{{end}}{{end}}{{end}}" \
    "ai-platform-$service")
  test -n "$volume"
  test "$("${DOCKER[@]}" volume inspect --format '{{.Driver}}' "$volume")" = local
  mountpoint=$("${DOCKER[@]}" volume inspect --format '{{.Mountpoint}}' "$volume")
  test -n "$mountpoint"
  printf '%s\t%s\t%s\n' "$service" "$volume" "$mountpoint" >> "$BACKUP/volume-map.txt"
  sudo -n tar --acls --xattrs --numeric-owner -C "$mountpoint" -cpf - . \
    > "$BACKUP/$service-volume.tar"
done

# Match the protected configuration and recorded actual mount sources.
WORKSPACE_ROOT=/absolute/path/to/configured-workspaces
LEGACY_ROOT=/absolute/path/to/configured-migration-source
if sudo -n test -d "$WORKSPACE_ROOT"; then
  sudo -n tar --acls --xattrs --numeric-owner -C "$WORKSPACE_ROOT" -cpf - . \
    > "$BACKUP/workspaces.tar"
else
  printf 'Current workspace root absent at recovery point\n' > "$BACKUP/workspace-state.txt"
fi
if sudo -n test -d "$LEGACY_ROOT"; then
  sudo -n tar --acls --xattrs --numeric-owner -C "$LEGACY_ROOT" -cpf - . \
    > "$BACKUP/legacy-workspaces.tar"
fi
age -r "$BACKUP_RECIPIENT" -o "$BACKUP/operator.env.age" "$ENV_FILE"
# Preserve the entire original immutable package, including every runtime template.
cp --no-clobber -- "$RUNNING_ARCHIVE" "$BACKUP/running-release.tar.gz"
# Encrypt the host configuration separately; adjust paths to the approved host layout.
sudo -n tar -C / -cpf - etc/ai-platform/opensandbox/server.env \
  etc/ai-platform/opensandbox/server.toml \
  | age -r "$BACKUP_RECIPIENT" -o "$BACKUP/opensandbox-config.tar.age"
(cd "$BACKUP" && sha256sum -- *.dump *.tar *.tar.gz *.age *.txt > SHA256SUMS)
(cd "$BACKUP" && sha256sum --check SHA256SUMS)
```

The saved original running-release archive retains all runtime templates, its
manifest/evidence and backup guide when present. Older packages may omit newer
evidence files; record that limitation. Do not substitute an incomplete tar of
selected Compose files for the original runnable package. Capture
host units/network policy using the same encrypted configuration procedure.
Redis 7 uses multipart AOF files and a manifest: retain its **entire** `/data`
volume, not only `dump.rdb` or a guessed `appendonly.aof`. Retain MinIO's entire
`/data` volume, including hidden `.minio.sys` metadata; a bucket-file copy is not
an equivalent backup. The extra cold PostgreSQL volume is tied to its original
major version and image; do not start it with a newer major PostgreSQL image.

Keep all files private, encrypt any copy leaving the encrypted backup filesystem,
and verify the off-host copy's checksums. A successful checksum or archive listing
does not prove logical consistency. Rehearse restoration before an upgrade.

The separate backup maintenance action stopped persistent services. Start the
**same** three containers and verify health before using the deployment entry:

```bash
"${DOCKER[@]}" start ai-platform-postgres ai-platform-redis ai-platform-minio
for service in postgres redis minio; do
  "${DOCKER[@]}" inspect --format '{{.Name}} {{.State.Status}} {{.State.Health.Status}}' \
    "ai-platform-$service"
done
```

Wait for all three to be healthy and compare their container IDs and mounts with
the recorded inventory. Keep admission and application writers stopped through
the upgrade or restore decision. If cancelling maintenance, reopening the old
application is a separate deliberate action after confirming no migration ran.
The package's persistent-service preservation check starts with its own later
snapshot; it does not promise that this cold-backup procedure avoids downtime.

## 3. Rehearse on an isolated restore host

Use a separate, approved host with no production network access, no production
OpenSandbox access, and no application writers. Do not start a second production
Compose project on the original host. The retained container and persistent-volume
identities make that unsafe. Copy the recovery set onto encrypted private
storage and verify `sha256sum --check SHA256SUMS` there.

Prepare a new mode `0600` file containing only `POSTGRES_USER`, `POSTGRES_DB`, and
`POSTGRES_PASSWORD` for this isolated PostgreSQL instance using a secure editor or
secret manager. Use the recorded database/user names; preserve the production
credential only if the authorized isolated test requires it. Never put a password
on a command line. `RESTORE_POSTGRES_ENV` below must be a Docker env-file with
literal values, not a shell script or an interpolated Compose env file.

```bash
set -euo pipefail
umask 077
DOCKER=(docker) # Use the authorized command on the isolated host.
BACKUP=/mnt/encrypted-restore/selected-recovery-set
RESTORE_POSTGRES_ENV=/absolute/private/path/restore-postgres.env
: "${POSTGRES_IMAGE:?set the recorded repository@sha256 digest}"
: "${REDIS_IMAGE:?set the recorded repository@sha256 digest}"
: "${MINIO_IMAGE:?set the recorded repository@sha256 digest}"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
PREFIX="ai-platform-rehearsal-$STAMP"
# Names must be new; never select an original or existing data volume.
for service in postgres redis minio; do
  if "${DOCKER[@]}" volume inspect "$PREFIX-$service" >/dev/null 2>&1; then
    echo 'Restore volume already exists; stop and choose a new isolated recovery set.' >&2
    exit 1
  fi
  "${DOCKER[@]}" volume create "$PREFIX-$service" >/dev/null
done
"${DOCKER[@]}" run -d --name "$PREFIX-postgres" --network none \
  --pull never --env-file "$RESTORE_POSTGRES_ENV" \
  --mount "type=volume,source=$PREFIX-postgres,target=/var/lib/postgresql/data" \
  "$POSTGRES_IMAGE" >/dev/null
# Wait for readiness; do not restore until this succeeds.
"${DOCKER[@]}" exec "$PREFIX-postgres" sh -ceu \
  'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
"${DOCKER[@]}" exec -i "$PREFIX-postgres" sh -ceu \
  'pg_restore --exit-on-error --single-transaction --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$BACKUP/postgres.dump"
"${DOCKER[@]}" exec "$PREFIX-postgres" sh -ceu \
  'psql -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select version, checksum_sha256 from schema_migrations order by version"' \
  > "$BACKUP/rehearsal-schema-versions.txt"
cmp "$BACKUP/schema-versions.txt" "$BACKUP/rehearsal-schema-versions.txt"

# Redis and MinIO must still have no container using these new volumes.
for service in redis minio; do
  mountpoint=$("${DOCKER[@]}" volume inspect --format '{{.Mountpoint}}' "$PREFIX-$service")
  sudo -n tar --acls --xattrs --numeric-owner --same-owner --same-permissions \
    -C "$mountpoint" -xpf "$BACKUP/$service-volume.tar"
done
RESTORED_WORKSPACES="/mnt/encrypted-restore/$PREFIX-workspaces"
if test -f "$BACKUP/workspaces.tar"; then
  sudo -n mkdir -m 700 "$RESTORED_WORKSPACES"
  sudo -n tar --acls --xattrs --numeric-owner --same-owner --same-permissions \
    -C "$RESTORED_WORKSPACES" -xpf "$BACKUP/workspaces.tar"
fi
```

Check the restored workspace root's numeric owner/mode, ACLs and xattrs against
its archive, including dotfiles and migration markers. Rehearse legacy-source
restoration into a different new directory if present. Do not remove the original
source or copy an incomplete target over it.

Start Redis against its restored volume with the **recorded digest**, no ports,
`--network none`, and `redis-server --appendonly yes`. Start MinIO with its recorded
digest, the recorded command/user and a protected env-file containing only its
required credentials, also with no ports and `--network none`. For example:

```bash
"${DOCKER[@]}" run -d --name "$PREFIX-redis" --network none --pull never \
  --mount "type=volume,source=$PREFIX-redis,target=/data" \
  "$REDIS_IMAGE" redis-server --appendonly yes >/dev/null
"${DOCKER[@]}" exec "$PREFIX-redis" redis-cli ping
"${DOCKER[@]}" exec "$PREFIX-redis" redis-cli INFO persistence \
  > "$BACKUP/rehearsal-redis-persistence.txt"
: "${RESTORE_MINIO_ENV:?set the private mode-0600 MinIO env-file path}"
"${DOCKER[@]}" run -d --name "$PREFIX-minio" --network none --pull never --user 0:0 \
  --env-file "$RESTORE_MINIO_ENV" \
  --mount "type=volume,source=$PREFIX-minio,target=/data" \
  "$MINIO_IMAGE" server /data --console-address :9001 >/dev/null
"${DOCKER[@]}" exec "$PREFIX-minio" mc ready local
```

Wait for service readiness and review private startup diagnostics for AOF or
storage corruption. Do not accept Redis startup if it reports discarded/truncated
AOF data; record and resolve any integrity warning. Never run `redis-check-aof
--fix` on the original or only backup. Test representative DB records, encrypted
model/MCP credential decryption with the original keys, MinIO bucket/object
counts and sample content checksums, Redis stream/queue state, and workspace
ownership/content. Record sample scope, actual results, timestamps, and failures.
These prefixed rehearsal containers/volumes are deliberately separate from the
production topology; they are not valid inputs to `deploy.py`. It refuses orphan
data volumes or a partial normal stack, and `--resume-install` is unavailable
without the original matching journal. Full topology reconstruction is an
operator-classified prerequisite, not a documented automatic bypass.

A full application rehearsal additionally needs an approved isolated network,
the backup-compatible package/schema and restored host policy. Permit only the
intended test dependencies; do not let restored workers execute production jobs.

## 4. Choose recovery deliberately

- Before any deployment mutation: correct failed prerequisites and retry normally.
- Data-only interrupted first install: `--resume-install` requires no application
  containers, even stopped ones; the intact owner-held mode `0600`
  `.ai-platform-install-state.json` beside the env file; and the exact source
  commit, rendered configuration fingerprint and workspace-migration mode. It
  preserves existing data. `--resume-install --check` needs PostgreSQL already
  running and all package images cached; it never starts PostgreSQL.
- Changed configuration (including corrected secrets), missing/mismatched journal,
  orphan data volumes, partial activity schema, unsupported storage layout, or
  any application containers after an interrupted first install: operator
  classification is required. Do not fabricate a journal or bypass the refusal.
- Workspace migration failure: retain the source and incomplete marker. Continuing
  requires the original source and every existing target entry to match it. Any
  existing supported legacy source directory, **even empty**, requires explicit
  `--migrate-legacy-workspaces`; an absent source on a fresh/current-layout host
  skips migration. A retained source may require the flag again on a later run.
- Schema migration or later startup failure: keep admission stopped, preserve the
  failed state, determine the last completed schema migration and use a proven
  schema-compatible corrected release or a separately authorized full restore.

There is **no automatic database or image rollback**. The Redis Streams SSE
cutover drops retired state after explicit legacy-state retirement. Schema
`2026.09.12.1` is incompatible with an older backend image. Before that cutover,
finish/cancel old Runs, stop producers, and use the new verified backend's
`/app/tools/retire_legacy_sse_streams.py` inventory/apply procedure described in
`README.md`; the normal deployment entry does not perform retirement for you.

For an authorized production restore, first preserve the failed state as a
separate recovery set. Approve the destination, backup/schema/package pairing,
accepted data-loss interval, credentials and cutover plan. Restore the matching
PostgreSQL, Redis, MinIO and workspace set into new storage while keeping originals
intact. Switching production mounts/host paths is a classified operation outside
`deploy.py`; its identity guards must not be bypassed by a second project or an
empty workspace root. Never reconnect an older DB to mismatched newer objects or
workspaces. Verify migration ledger/checksums without editing them, image identity,
API readiness, advancing Worker heartbeat, OpenSandbox isolation and quiescence
before reopening admission. Keep originals until the explicitly approved retention
period ends. A rehearsal does not authorize production cutover or deletion.
