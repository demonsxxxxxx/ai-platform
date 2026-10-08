# Deploy an internal-test Release

Use only `ai-platform-internal-test.tar.gz` from an immutable official Release.
Its `deploy.py`, `compose.yaml`, `compose.override.yaml`, pinned image manifest,
release evidence and `BACKUP-RESTORE.md` belong to the same commit. Do not repack
this archive or mix it with the earlier production package. Keep the existing
`/data/ai-platform-internal-test` configuration, volumes and workspace identity.
The controller's activity gate, migration and workspace initialization remain
mandatory; schema migration does not automatically roll back.

This package selects `DEPLOYMENT_ENVIRONMENT=test`, `SANDBOX_SECURITY_PROFILE=internal-test`
and the native OpenSandbox server proxy. The host OpenSandbox server must use
`network_mode = "bridge"` and `docker_runtime = "runsc"`; do not enable its
NAT-redirect egress sidecar (incompatible with gVisor). The bridge does **not**
provide the production host firewall guard: sandbox processes may reach network-
accessible public, private, metadata and host addresses. Use only on a trusted,
isolated test host with appropriate external network controls. This package
cannot deploy a production environment or establish production-equivalent
network isolation.

The OpenSandbox API URL/key, server proxy, image digest, workspace host bind,
Run/Attempt identity, callback token and model proxy capability remain checked.
Set `OPENSANDBOX_DOMAIN` and `OPENSANDBOX_PROTOCOL` for the private lifecycle
server (or a valid `OPENSANDBOX_BASE_URL`). Its address must be a private
non-loopback IPv4 literal reachable from the API and Worker containers.
Configure
`SANDBOX_CALLBACK_BASE_URL` must be the explicit
`http://<bridge-gateway>:<published-API-port>`; the Compose-only
`api.sandbox.internal` alias is not reachable from ordinary Docker `bridge`.
Set `OPENSANDBOX_EGRESS_PROXY_URL` to the host endpoint reachable from the
ordinary OpenSandbox bridge. Bind the proxy's port 18043
using `OPENSANDBOX_EGRESS_PROXY_BIND_ADDRESS` on the Docker `bridge`
gateway address. The package controller compares that host bind with the
actual bridge gateway and the API/Worker `OPENSANDBOX_EGRESS_PROXY_URL`
(`http://<bridge-gateway>:18043`); do not publish it on 0.0.0.0.
`SANDBOX_RUNTIME_SUBJECT` and `SANDBOX_WORKSPACE_ROOT` must match the existing
test installation. Both API and Worker need the same model encryption and
callback credentials; the existing `SANDBOX_CALLBACK_TOKEN` must contain at
least 32 characters because it also derives test executor control
authentication. Preserve it across restarts. Model connection contents remain
administrator-owned and are not stored in this package.

For private-CA ProfileDrive HTTPS imports, retain the existing public CA file
and set both `PROFILE_DRIVE_TRANSFER_CA_CERT_HOST_FILE` (absolute, readable host
file) and `PROFILE_DRIVE_TRANSFER_CA_CERT_FILE` (API container path). The
optional packaged CA overlay mounts only that certificate read-only. Preflight
rejects missing, linked, malformed, group/world-writable and private-key files
and verifies the mount as the API user. Never mount a private key or replace the
existing certificate without its approved renewal procedure.

Keep the existing externally owned `.env` (mode 0600), data volumes, PostgreSQL,
Redis, MinIO and workspace directory. Do not copy `.env.example` over it, create
replacement secrets, or remove legacy workspaces. Follow the coordinated backup
and restore requirements in `BACKUP-RESTORE.md` before stateful migration.
From an extracted official package, check with:

```sh
python3 deploy.py --env-file /path/to/existing/.env --check --allow-insecure-http
```

Use `--allow-insecure-http` only when the existing intranet browser origin is
HTTP and the risk is accepted. Once preflight, backups and a maintenance window
are ready, run the same package controller without `--check`. Independently
verify the commit/digests, retained data service identities, workspace bind,
real `runsc` task, callback, network behavior and persisted-lease cleanup before
retiring the previous Release. Tenant-wide admin orphan cleanup intentionally
does not delete untracked OpenSandbox containers; classify unknown stopped
containers separately without broadening the deletion scope. See the
repository release operations runbook for the full procedure; a healthy
controller result alone is not Sandbox acceptance.
