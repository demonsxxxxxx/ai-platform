# Production host preparation

This document retains the dedicated-network host contract for controlled
maintenance of hosts already using it. It is not an alternative application
package or a prerequisite for the current package's native Docker bridge.
Use the [single deployment guide](../../deploy/ai-platform/README.md) for the
application package. Its historical filename does not select a deployment track.
Do not switch an existing host's network, profile, credentials or workspace
identity to make it match a package without a separately reviewed migration.
The host operations below require explicit authority and matching runtime
configuration; this documentation does not authorize executing them.

## Host prerequisites

Use a Linux host with Python 3, Docker Compose v2, systemd, and a registered
`runsc` runtime. Provision the production OpenSandbox server through the reviewed
files in `deploy/opensandbox/`:

- `/etc/ai-platform/opensandbox/server.env` as `root:root` mode `0600`;
- `/etc/ai-platform/opensandbox/server.toml` as `root:<OPENSANDBOX_SERVER_GID>`
  mode `0640`; and
- the chosen application environment file as `root:root` mode `0600` for host
  preparation. Normal application deployment uses the invoking owner's file.

The TOML group is the dedicated server GID and is the only intentional
non-root-readable secret configuration. Ownership contract: `root:<OPENSANDBOX_SERVER_GID> 0640`.
Do not put real values in Git, issue text, package archives, or command output.

The host policy must use the private lifecycle address, the reviewed digest-bound
OpenSandbox server, the dedicated non-root identity, the Docker socket group,
the configured dedicated task network, `runsc`, digest-bound
execd and egress images, `dns+nft`, disabled IPv6 egress, the single
workspace-root Host-volume allowlist, and no global sandbox binds
or environment injection. The application derives one exact Attempt workspace
below that root; user, Agent and Skill input never supplies a host path. The
lifecycle API key is restricted to the reviewed plain URL-safe length. The
network guard must be installed, enabled, and active before the server unit starts.

Keep the four application topology values aligned with the protected host files:

| Application environment | Host configuration |
| --- | --- |
| `OPENSANDBOX_EXPECTED_NETWORK_MODE` | `server.toml`: `docker.network_mode` |
| `OPENSANDBOX_EGRESS_BRIDGE` | same key in `server.env` |
| `OPENSANDBOX_EGRESS_SUBNET` | same key in `server.env` |
| `OPENSANDBOX_EGRESS_PROXY_IPV4` | same key in `server.env` |
| `SANDBOX_WORKSPACE_ROOT` | sole exact entry in `storage.allowed_host_paths` |

The guard unit reads the three physical address/interface values from that same
root-owned `server.env`. Validation still inspects the installed unit, ordered
live IPv4/IPv6 rules and Docker network. A source example is not live acceptance.

Maintainer host preparation retains `production_bootstrap.HostBootstrap` for
secure configuration validation, unit rendering and host-service recovery, and
`opensandbox_unit_guard.py` for the unit's guarded container removal. These are
not application upgrade entry points. The former production release CLI and
its automatic application deployment have been removed; direct invocation
fails before host changes.
Host preparation takes a clean checkout at the exact declared commit and uses
its absolute unit-guard helper path. Keep that checkout unchanged while its unit
is installed, including the previous helper while rollback remains available.
The helper and every parent directory must be root-owned, nonsymlinked and have
no group/world write permissions, because systemd executes that helper as root.

The OpenSandbox server is a trusted host control-plane component. A read-only
Docker socket mount still grants effective Docker daemon authority, so accept
only the reviewed root-owned unit and exact runtime contour. The guard and the
unit must reject a foreign same-name server container. Host provisioning and
changes to credentials, addresses, images, or policy are separate maintenance
operations.

## Kernel and network isolation

The production topology combines gVisor (`runsc`) with network enforcement
outside the sandbox: a dedicated Docker bridge with host NAT, the host
INPUT/DOCKER-USER guard
and the stateless model/callback proxy. The SDK create request sends
`network_policy=None` in the unified runtime path. The server's retained
`[egress] mode = "dns+nft"` setting is a host configuration check, not evidence
that a per-sandbox egress sidecar is active.

OpenSandbox's [secure runtime guide](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/guides/secure-container.md#5-egress-sidecar-incompatible-with-gvisor)
documents the incompatibility between gVisor and its built-in egress sidecar.
That sidecar needs NAT redirect support inside the sandbox network stack.
The platform's host firewall uses the host kernel instead. Validate the actual
production contour with a sandbox created by the selected package: `runsc`
identity, successful public DNS/HTTPS and dependency download, denied private,
metadata, host and peer access, allowed model/callback proxy access, artifact
collection and cleanup. Verify both new and established proxy traffic and host
control-plane access to sandbox services. A healthy lifecycle listener alone
does not establish those properties.

The task network uses `internal=false`, IPv4 masquerading, `enable_icc=false`,
`enable_ipv6=false`. The examples use bridge `br-osb-egress2`, subnet
`172.31.76.0/24` and proxy `172.31.76.2:8080`; these are configurable defaults.
The host guard filters actual destination addresses before
public traffic reaches Docker's forwarding rules. IPv6 has separate scoped
INPUT/FORWARD guards. The proxy continues to expose only the existing model and
callback paths; it is not a general Internet proxy. Tasks access public services
directly, without domain approval prompts.

### Switch from the previous internal network

Docker network isolation and driver options cannot be changed in place. Use a
drained maintenance window and the matching immutable application package:

1. Stop new admission and dispatch, settle or cancel active Runs, then stop
   Workers and confirm that all previous sandbox endpoints have stopped.
   Keep PostgreSQL, Redis and MinIO
   volumes and business history. Old signed leases may be read and cleaned up,
   but cannot authorize new execution or renewal.
2. Stop the old proxy/application contour. Confirm that the old network has no
   endpoints before removing `ai-platform-opensandbox-egress-internal-v1`.
3. Install the reviewed guard unit, run `systemctl daemon-reload`, enable and
   restart the guard, and update the protected
   server TOML to the new network. The guard closes the new bridge while rules
   are refreshed and opens it only after both IP families are installed. A failed
   refresh leaves restrictive rules; retry after fixing the failure. A successful
   refresh removes all temporary holds, including those left by earlier attempts.
4. Start the matching package, which creates the new network and proxy. Verify
   the host unit, exact IPv4/IPv6 rules and network options before admission.
   Complete the runtime checks above before resuming Workers and new Runs.

Rollback also requires a drained window: restore the previous package, server
TOML and guard together, then verify the previous topology before admission.

Historical internal-test leases without the current `active-v1` marker remain
eligible for stop-only cleanup when their complete persisted scope, image,
executor identity and remote metadata match. They cannot be recreated,
reused, dispatched or renewed. The current package may create and renew new `active-v1` leases on its
native bridge; that configuration is not compatible with this dedicated-network
host contract. Missing historical identity evidence requires
classified recovery.

## Verify the host

Use privileged operations to verify, without printing configuration values:

```sh
systemctl is-active --quiet ai-platform-opensandbox-network-guard.service
systemctl is-active --quiet opensandbox.service
```

The application package also checks that `opensandbox.service` is active before
it changes application services. A configured lifecycle listener is private;
the local example uses port `18001` by default. The application CORS value must
be the browser-visible frontend origin.

## Application deployment boundary

The only current application package is `ai-platform-internal-test.tar.gz`.
Follow the [single deployment guide](../../deploy/ai-platform/README.md) and
[release runbook](release-operations-runbook.md), not a second production-package
procedure. The package fixes native Docker `bridge` and the `internal-test`
profile; these host templates retain a different dedicated-network security
contract. Do not deploy that bridge configuration onto a host that requires
this contract or treat an older immutable Release as schema-compatible without
verification. Host maintenance does not authorize application deployment,
network migration, credential replacement or rollback.
