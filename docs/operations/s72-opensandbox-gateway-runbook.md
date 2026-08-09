# s72 OpenSandbox Gateway Operations Runbook

This is the durable operations authority for the independently installed s72
OpenSandbox gateway. It covers only the root-owned gateway installation and
rollback on s72. It is separate from the 211 release runbook, does not change
the ai-platform provider by itself, and cannot establish a `211 verified` claim.

## Authority And Exact Source

Run `deploy/opensandbox/install-s72.sh` and
`deploy/opensandbox/rollback-s72.sh` only under a current release charter with
one release owner and one mutation lease. The release owner resolves the exact
remote authority commit before root mutation, records a non-secret authority
evidence ID, and supplies both to the script. The root scripts do not fetch and
must not receive repository credentials.

The source argument to `install-s72.sh` is a root-owned, clean source checkout:
its real path is non-symlinked, every path is root-owned, `HEAD`,
`refs/remotes/origin/main`, and
`OPENSANDBOX_GATEWAY_EXPECTED_AUTHORITY_SHA` are the same 40-character commit.
The installer archives only the gateway and deployment subjects into an immutable
root-owned release below `/opt/opensandbox-gateway/releases/<commit>`, verifies
its manifest, applies the sealed desired state through the recovery transaction,
and commits only after the current pointer, service state, and listener are all
revalidated.

Use the live scripts, with values obtained through the authorized host procedure:

```sh
sudo env \
  OPENSANDBOX_GATEWAY_EXPECTED_AUTHORITY_SHA=<fresh-main-commit> \
  OPENSANDBOX_GATEWAY_AUTHORITY_EVIDENCE_ID=<non-secret-fresh-evidence-id> \
  deploy/opensandbox/install-s72.sh /path/to/root-owned-clean-ai-platform-clone
```

Do not replace this with a manual unit, copy, symlink, Git checkout, or service
mutation. A mismatch, dirty source, stale local authority ref, unsafe ownership,
or unavailable lock fails closed.

## Configuration And Pinned CA

Before installation, `/etc/opensandbox-gateway` and its `secrets/` and `tls/`
directories must be root-owned, non-symlinked `0750` directories. The installer
requires these regular files, then normalizes the runtime copy to the gateway
group without widening modes:

| Subject | Required mode |
| --- | --- |
| `gateway.env`, `egress-policy.v1.json`, `tls/fullchain.pem`, `tls/upstream-ca.pem` | `0640` |
| `tls/privkey.pem`, `secrets/lifecycle-api-key`, `secrets/capability-token`, `secrets/record-signing-key` | `0440` |

The gateway bridge policy pins its callback, OpenAI, and Anthropic destinations
to the approved 211 address and fixed hostname. `tls/upstream-ca.pem` is the
dedicated non-secret bridge CA certificate used only by the gateway trust
context. Keep the CA private key offline; do not install this CA in a system
trust store, substitute a leaf or system bundle, or put certificate/key bytes in
Git, environment variables, logs, or terminal evidence.

Secrets are files, never `gateway.env` values or command output. Report only
redacted metadata and non-secret authority evidence; do not print, copy, export,
or retain secret, certificate-key, configuration, or private payload contents.

## Mandatory Remote Smoke Before Provider Switch

Installation alone is not authority to select the OpenSandbox provider on
ai-platform. Before an ai-platform provider switch, run the approved remote
smoke from s72 using the configured CA and a disposable scope. It must prove the
fixed 211 hostname/IP and TLS validation, denial for wrong hostname/CA/pinned IP
or source, callback and both model prefixes with the expected rewritten paths,
and the OpenSandbox runtime boundary: runsc, `network_mode=none`,
no-new-privileges, scoped mounts, attestation, cancellation, and bounded orphan
cleanup. Keep Docker selected when any check fails or is unavailable; do not add
sandbox egress or bypass attestation to make a smoke pass.

The separate 211 release authority owns any provider transition. Record only
redacted, subject-bound remote evidence through that authorized procedure.

## Snapshot, Rollback, And Recovery

`install-s72.sh` first opens and locks the trusted, pre-existing
`/run/lock/opensandbox-gateway-s72-install.lock`; it performs no persistent
bootstrap before this lock is held. It creates a private transaction-owned
snapshot stage below `/var/lib/opensandbox-gateway-deploy/snapshots`, on the same
filesystem as the final snapshot. The stage has a closed typed inventory: only
the declared unit, configuration, ACL, authority, current-pointer, rollback-pointer,
lifecycle, manifest, and marker payloads are allowed. Marker and payload presence
is biconditional. Symlinks, FIFOs, sockets, devices, unknown entries, mixed marker
states, and foreign or replaced inodes fail closed.

Every snapshot file and directory is flushed before `MANIFEST.identity` and
`SNAPSHOT.seal` are created in the private stage. The already sealed stage is
published by one same-parent rename, the snapshots parent is flushed, and the
published root identity and seal are checked again. A manifest never describes a
different pre-publication root.

Each install or rollback has one immutable, self-authenticating transaction-record
chain under `/var/lib/opensandbox-gateway-deploy/transactions`. Records bind the
operation, sequence, phase, source and destination commits, non-secret evidence,
recovery and apply snapshots, stage identity, and prior record seal. The supported
phases cover reservation, snapshot and release publication, staging, stop intent,
the gateway account/group/runtime-directory identity intents, each restore mutation,
revalidation, runtime restore, commit, and cleanup. A torn,
unknown, out-of-order, replaced, or corrupt record is preserved and rejected; it
is never repaired by deleting or editing state.

The normal install chain is `reserved -> snapshot-published ->
identity-group-intent -> identity-group-ready -> identity-user-intent ->
identity-user-ready -> identity-runtime-intent -> identity-ready ->
release-published -> staged -> stop-intent -> stopped -> identity-applied -> units-applied ->
config-applied -> acl-applied -> authority-applied -> pointer-applied ->
current-applied -> revalidated -> runtime-restored -> committed -> cleaned`.
Rollback omits `release-published`; crash recovery records `recovering` before it
re-enters at `staged`. Only the transitions encoded by this chain are valid.

On process death or a partial install/rollback failure, run only the supported
recovery entry after reacquiring the same mutation lease:

```sh
sudo deploy/opensandbox/install-s72.sh --recover
```

Recovery verifies the sealed snapshot and transaction chain, accepts live files
only when they match either the recovery or apply snapshot, then resumes the
journaled restore. Private stages are removed only when their recorded transaction
and root device/inode still match. An unknown pre-existing or replaced object is
left untouched and recovery fails closed. If automatic recovery cannot finish,
the transaction and its unique recovery snapshot remain authoritative evidence;
do not manually delete, rename, or edit them.

Use the live rollback script only with a freshly resolved authority SHA and a
new non-secret evidence ID:

```sh
sudo env \
  OPENSANDBOX_GATEWAY_EXPECTED_AUTHORITY_SHA=<fresh-main-commit> \
  OPENSANDBOX_GATEWAY_AUTHORITY_EVIDENCE_ID=<non-secret-fresh-evidence-id> \
  deploy/opensandbox/rollback-s72.sh
```

Rollback verifies the root-owned snapshot manifest and seal, confined release
path, recorded release provenance, and the source/evidence identity captured in
the snapshot. The freshly supplied authority still gates the current source, but
cannot rewrite the sealed rollback subject. Before the first stop or file change,
the engine validates the current units, configuration, ACL and pointer identities,
the exact `opensandbox.service` fragment, and exactly one `LISTEN`
`127.0.0.1:8080` endpoint. Substring ports such as `80800` or `18080`, wildcard
listeners, IPv6 aliases, duplicates, missing listeners, and lifecycle drift fail
before mutation.

The transaction restores the prior units, configuration, ACL, authority state,
enable/active state, and release pointer (or their prior absence), revalidates the
complete filesystem state before restart, and verifies lifecycle/listener state
again before commit. Unit restore requires the exact recorded `UnitFileState`,
`LoadState`, and `ActiveState`; enable or disable failure and post-command drift
leave the transaction uncommitted. Snapshot capture accepts only exact `loaded`
units whose active state is `active` or `inactive` and whose unit-file state is
`enabled` or `disabled`, plus exact absent units reported as `not-found`,
`inactive`, and an empty unit-file state. Query errors and states such as
`failed`, `activating`, `static`, `masked`, `linked`, or `enabled-runtime` fail
before mutation. The configured gateway UID binds the exact
system group, account, home, shell, empty membership, and `0700` runtime directory.
Creation intent is sealed before account mutation, and a new runtime directory is
published from a transaction-owned private stage only after its device/inode is
recorded. Recovery removes only exact objects created by that transaction; a
pre-existing mismatch or later foreign replacement is preserved and fails closed.
Immediately before and after a non-force account deletion, and again before group
deletion, the installer strictly enumerates real, effective, saved, and filesystem
UIDs for the bounded Linux process table. Any matching live process, malformed
row, enumeration error, or empty enumeration refuses deletion or further
transaction advancement; it never kills a process.
Absent unit/config snapshots are accepted only with their
sealed lifecycle authority; otherwise they fail closed. It never changes ai-platform
provider configuration, deletes workspaces or SQLite runtime state, or replaces
the separate 211 release/rollback authority. Suspected secret exposure requires
the designated security response and downstream secret rotation before further
use.

Repository tests and required Ubuntu CI prove source contracts, POSIX node and
race handling, transaction replay, and complete module collection. They do not
prove a live systemd/Docker deployment, a registry artifact, an s72 host rollback,
or 211 runtime acceptance. Those remain separate authorized host gates.
