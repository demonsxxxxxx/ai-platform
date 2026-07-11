# S1B-B Non-Root Runtime Identity Design

Status: approved for implementation

Tracking issue: [#394](https://github.com/demonsxxxxxx/ai-platform/issues/394)

Authoritative base: `c854085f916748ca3c34c8a01bfc6a505b8dca5b`

## Goal

Run the API, worker, Docker sandbox executor, and OpenSandbox executor as the
dedicated identity `10001:10001`, while preserving tenant/workspace/user/session/run
isolation and safe read/write access to sandbox workspaces. Source evidence remains
capped at `source/local partial`; Docker and 211 acceptance belong to the controller.

## Non-Goals

- Do not change S1B-A execution routing, Claude worker/runner behavior, permission
  broker semantics, callback policy, egress policy, schema, repositories, queue
  fencing, frontend code, or real deploy environment values.
- Do not add a default Docker socket mount, privileged mode, setuid binaries,
  `chmod 777`, or another world-writable workaround.
- Do not claim an OpenSandbox SDK user field that does not exist in SDK `0.1.13`.

## Fixed Runtime Identity

`10001:10001` is a source constant, not a deploy setting. The Docker image creates
the `ai-platform` group and user with these numeric identifiers, owns only its home,
runtime temp, cache, and config directories, and ends with `USER 10001:10001`.
Compose repeats `user: "10001:10001"` for API and worker so image metadata and
deployment intent are both auditable. The ordinary entrypoint checks effective UID
and GID before exec. There is no environment switch for another runtime identity.

The image sets `HOME`, `TMPDIR`, `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, and
`XDG_DATA_HOME` to directories owned by the runtime identity. `/app` remains
root-owned and readable/executable, not a runtime write target.

## Workspace Initialization And Migration

A dedicated `workspace-init` compose service runs a fixed Python module entrypoint.
It mounts only the target workspace at `/runtime-workspaces`, uses no network, uses
a read-only root filesystem, drops all capabilities, and adds only `CHOWN`,
`DAC_READ_SEARCH`, `SETUID`, and `SETGID`. API and worker depend on its successful
one-shot completion with `service_completed_successfully`.

The initializer accepts no path argument. It opens the fixed root without following
symlinks and walks it using directory descriptors and `lstat`-equivalent operations.
Every entry must stay on the root device, be a regular file or directory, and be
owned by either `0:0` or `10001:10001`. Symlinks, sockets, devices, FIFOs, foreign
non-root ownership, cross-device entries, unexpected permission bits, and traversal
errors fail closed before ownership changes. It does not chmod.

After validation, it changes root-owned entries to `10001:10001` without following
links. It then clears supplementary groups, sets GID and UID to the runtime identity,
and creates, reads, and deletes a sentinel in the workspace root. Failure leaves API
and worker blocked. Existing 211 volume ownership must be read back by the controller
before deployment; source code does not assert that migration already occurred.

The explicit sandbox overlay replaces the initializer mount with the same host bind
used by the worker. Only the worker receives the socket mount and the required
non-secret `DOCKER_SOCKET_GID` supplementary group. The default compose remains
socket-free.

## Executor Identity Contract

The sandbox executor exposes a separate internal runtime-identity endpoint. It uses
the existing lease-bound executor credential and returns only exact effective UID and
GID. The public `/health` response remains unchanged. The internal response contains
no environment, path, tenant, workspace, run, command, or secret data.

The Docker provider requires the workspace directory to be owned by `10001:10001`
before container creation. Stat failure, UID zero, invalid GID, or any mismatch raises
a typed provider error. Container creation always passes `user="10001:10001"`.
After startup the provider checks Docker `Config.User` and calls the authenticated
runtime-identity endpoint. Any mismatch causes immediate stop/remove and no lease.

Reuse revalidates scope labels, workspace ownership, expected identity labels,
Docker `Config.User`, executor credential presence, and the authenticated process
identity. A stale or mismatched container is never silently adopted.

OpenSandbox `Sandbox.create()` has no user argument in SDK `0.1.13`; the provider
does not invent one. It relies on the fixed-USER image and ordinary entrypoint, then
requires the same authenticated runtime-identity response. Missing support, UID/GID
zero, mismatch, or malformed evidence kills and closes the sandbox. Cached reuse is
revalidated rather than returned blindly.

## Evidence And Projection Boundary

Exact UID/GID and verification method may be retained in provider labels and private
lease evidence for Admin operations. Ordinary user projections continue to expose
only `/workspace` and `/workspace/inputs`; container-started events remain admin-only
and do not include host paths or runtime identity details.

Source tests can prove code and configuration contracts. The controller must prove
the built image `Config.User`, compose container identities, actual `id -u`/`id -g`,
workspace migration and I/O, Docker executor `Config.User`, OpenSandbox process
identity, cancel cleanup, and 211 source/runtime parity.

## Verification Strategy

TDD covers initializer validation and migration, image/compose contracts, entrypoint
identity checks, Docker fail-closed owner handling, exact create/reuse identity,
OpenSandbox unsupported/mismatched identity denial, workspace I/O and cleanup,
S1B-A real-sandbox routing, socket/privilege/permission prohibitions, and public
projection/source-authority boundaries. Local Docker is not used.
