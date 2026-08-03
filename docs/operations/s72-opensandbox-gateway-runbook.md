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
its manifest, and atomically changes `current` only after the service checks.

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

`install-s72.sh` holds `/var/lib/opensandbox-gateway-deploy/install.lock` and
creates a root-only snapshot of gateway units, configuration, workspace ACL,
authority state, and the current release pointer before mutation. On an install
failure it restores that snapshot; if restoration fails, it preserves the unique
recovery snapshot and exits fail-closed for operator recovery. Do not delete or
edit that snapshot to continue an installation.

Use the live rollback script only with a freshly resolved authority SHA and a
new non-secret evidence ID:

```sh
sudo env \
  OPENSANDBOX_GATEWAY_EXPECTED_AUTHORITY_SHA=<fresh-main-commit> \
  OPENSANDBOX_GATEWAY_AUTHORITY_EVIDENCE_ID=<non-secret-fresh-evidence-id> \
  deploy/opensandbox/rollback-s72.sh
```

Rollback verifies the root-owned snapshot manifest, confined release path,
recorded release provenance, and that the rollback release remains an ancestor
of the supplied fresh authority. It restores the prior units, configuration,
ACL, authority state, enable/active state, and release pointer (or their prior
absence), then rechecks local OpenSandbox health. It never changes ai-platform
provider configuration, deletes workspaces or SQLite runtime state, or replaces
the separate 211 release/rollback authority. Suspected secret exposure requires
the designated security response and downstream secret rotation before further
use.
