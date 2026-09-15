# Production host preparation

The application package is the normal production upgrade path. This document
covers only the one-time host preparation and controlled maintenance of the
independently managed OpenSandbox service. It does not ask an operator to keep
a source checkout or rebuild application images.

## Host prerequisites

Use a Linux host with Python 3, Docker Compose v2, systemd, and a registered
`runsc` runtime. Provision the production OpenSandbox server through the reviewed
files in `deploy/opensandbox/`:

- `/etc/ai-platform/opensandbox/server.env` as `root:root` mode `0600`;
- `/etc/ai-platform/opensandbox/server.toml` as `root:<OPENSANDBOX_SERVER_GID>`
  mode `0640`; and
- `/data/ai-platform-prod/config/production/.env` as `root:root` mode `0600`.

The TOML group is the dedicated server GID and is the only intentional
non-root-readable secret configuration. Ownership contract: `root:<OPENSANDBOX_SERVER_GID> 0640`.
Do not put real values in Git, issue text, package archives, or command output.

The host policy must use the private lifecycle address, the reviewed digest-bound
OpenSandbox server, the dedicated non-root identity, the Docker socket group,
the `ai-platform-opensandbox-egress-internal-v1` network, `runsc`, digest-bound
execd and egress images, `dns+nft`, disabled IPv6 egress, no host bind mounts,
and no global sandbox binds or environment injection. The lifecycle API key is
restricted to the reviewed plain URL-safe length. The network guard must be
installed, enabled, and active before the server unit starts.

Maintainer host preparation retains `production_bootstrap.HostBootstrap` for
secure configuration validation, unit rendering and host-service recovery, and
`opensandbox_unit_guard.py` for the unit's guarded container removal. These are
not application upgrade entry points. The former production release CLI and
its automatic application deployment have been removed; direct invocation
fails before host changes.

The OpenSandbox server is a trusted host control-plane component. A read-only
Docker socket mount still grants effective Docker daemon authority, so accept
only the reviewed root-owned unit and exact runtime contour. The guard and the
unit must reject a foreign same-name server container. Host provisioning and
changes to credentials, addresses, images, or policy are separate maintenance
operations.

## Kernel and network isolation

The production topology combines gVisor (`runsc`) with network enforcement
outside the sandbox: the internal Docker bridge, host INPUT/DOCKER-USER guard
and the stateless model/callback proxy. The SDK create request sends
`network_policy=None` in both supported profiles. The server's retained
`[egress] mode = "dns+nft"` setting is a host configuration check, not evidence
that a per-sandbox egress sidecar is active.

OpenSandbox's [secure runtime guide](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/guides/secure-container.md#5-egress-sidecar-incompatible-with-gvisor)
documents the incompatibility between gVisor and its built-in egress sidecar.
That sidecar needs NAT redirect support inside the sandbox network stack.
The platform's host firewall uses the host kernel instead. Validate the actual
production contour with a sandbox created by the selected package: `runsc`
identity, denied direct external/host/peer access, allowed model/callback proxy
access, artifact collection and cleanup. A healthy lifecycle listener alone
does not establish those properties.

The internal-test package selects ordinary `bridge`, disables governed egress
and permits its explicit model-credential forwarding exception. Editing
`DEPLOYMENT_ENVIRONMENT` in its env file does not convert it to production;
the Compose profile fixes that value. Moving to production requires the matching
host topology, production package and runtime acceptance.

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

## Install or upgrade the application

Download the matching immutable production package from one Deployment Release,
extract it, and reuse the owner-held application environment file. From the
package directory run:

```sh
python3 deploy.py \
  --env-file /data/ai-platform-prod/config/production/.env \
  --docker-cmd 'sudo -n docker'
```

The package validates immutable application and data image identities, active
work protection, deployment mutual exclusion, migration, workspace
initialization, API/Worker/OpenSandbox health, and persistent-service identity.
It does not delete volumes or silently roll back an advanced schema. Independent
post-deployment acceptance must confirm the target image/commit, API readiness,
OpenSandbox checks, two advancing Worker heartbeat samples, `migrate` and
`workspace-init` exit 0, unchanged PostgreSQL/Redis/MinIO identity and restart
counts, no active work, and controller termination.

Use the package's `--check` before a maintenance window. Do not run the retired
source-checkout release controller or reconstruct a latest Release on the host.
