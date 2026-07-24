# s72 OpenSandbox Gateway v1 Runbook

## Purpose and boundary

This independently deployed HTTPS adapter is the only public listener for the
s72 OpenSandbox lifecycle. OpenSandbox itself remains on
`http://127.0.0.1:8080`, and ai-platform's Docker provider remains the default
and rollback path. The gateway does not alter OpenSandbox upstream and exposes
no generic forward proxy.

The source contract is `GatewayApplication.handle(Request) -> Response`.
Production injects loopback lifecycle HTTP, Docker/runsc evidence, SQLite
sealed state, and the scoped-workspace mailbox broker. Tests inject the same
contract through in-memory adapters.

## Exposed surface

- unauthenticated `GET /healthz` and `GET /readyz`;
- bearer-authenticated `GET /v1/capabilities/external-egress`;
- API-key-authenticated create/get/list/delete, `POST .../cancel`, fixed endpoint
  discovery, fixed execd/executor proxy routes, and
  `GET /v1/sandboxes/{id}/attestation`;
- no route for OpenSandbox's generic proxy, logs, metrics, pause, renew,
  ingress, credential proxy, arbitrary extensions, or sandbox egress.

Every create binds a unique tenant/workspace/user/session/run scope to an
immutable image, runsc, `network_mode=none`, no-new-privileges, a scoped
workspace mount, the configured gateway/callback/capability/deny subjects, and
a sealed non-secret lease record. Attestation re-reads OpenSandbox metadata and
Docker/image/mount/runtime state. Dispatch and reuse repeat that verification.

The sandbox-local relay listens only on `127.0.0.1:18888` and exchanges bounded
regular files under the already scoped workspace. The host broker selects one
of three configured HTTPS destinations (callbacks, OpenAI-compatible model, or
Anthropic-compatible model), verifies the TLS hostname, requires a pinned IP,
rejects redirects, and never accepts a caller-selected remote host. Secrets
remain in the sandbox request and are neither logged nor persisted in gateway
state.

## Pre-deployment gate

Do not deploy until all of the following are current on s72:

1. OpenSandbox is the expected 0.2.1 service, active only on
   `127.0.0.1:8080`, with `network_mode=none`, runsc, no-new-privileges, and the
   sole allowed host root `/data/opensandbox/workspaces`.
2. `OPENSANDBOX_GATEWAY_PUBLIC_AUTHORITY` is the approved literal private s72 IP
   and TLS port. The certificate contains that exact address as an IP SAN (a DNS
   SAN or CN is insufficient), the complete chain validates, and only that TLS
   gateway port is reachable from ai-platform. Keep the ai-platform endpoint in
   the same literal-IP form; do not broaden it to caller-controlled DNS.
3. Three independent secret files contain newly provisioned lifecycle API key,
   capability bearer token, and at least 32 bytes of record-signing key. Never
   place their values in `gateway.env` or operator output.
4. `gateway.env` subjects exactly equal the ai-platform configuration and the
   immutable executor reference embeds the same `sha256` digest.
5. Each egress policy URL exactly equals its matching environment base and each
   IP is a current, operator-approved address for that hostname.

Copy `gateway.env.example` and `egress-policy.v1.example.json` to their paths
under `/etc/opensandbox-gateway`, replace every `REQUIRED` marker, place TLS and
secret files at the referenced paths, then inspect them without printing secret
contents:

```sh
grep -R 'REQUIRED' /etc/opensandbox-gateway/gateway.env /etc/opensandbox-gateway/egress-policy.v1.json
stat -c '%a %U:%G %n' /etc/opensandbox-gateway/secrets/* /etc/opensandbox-gateway/tls/*
```

The first command must return no matches. The installer accepts only an exact
40-character lowercase commit from a clean, root-owned, non-symlink Git tree.
For a new install or upgrade, the release owner must freshly resolve the exact
remote `main` SHA before the checkout is sealed root-only. `HEAD`, the local
`refs/remotes/origin/main`, and that explicit 40-hex SHA must all match; a stale
local ref or older ancestor is insufficient. The root installer never fetches or
receives repository credentials. Unset `OPENSANDBOX_GATEWAY_AUTHORITY_REF`
unless an explicitly reviewed equivalent remote ref is required:

```sh
# Run these credentialed operations as the release owner, before root sealing.
EXPECTED_AUTHORITY_SHA=$(git -C /path/to/reviewed/ai-platform ls-remote --exit-code origin refs/heads/main | awk 'NR == 1 {print $1}')
test "${#EXPECTED_AUTHORITY_SHA}" -eq 40
git -C /path/to/reviewed/ai-platform fetch origin main
test "$(git -C /path/to/reviewed/ai-platform rev-parse 'refs/remotes/origin/main^{commit}')" = "$EXPECTED_AUTHORITY_SHA"
AUTHORITY_EVIDENCE_ID="ls-remote-$(date -u +%Y%m%dT%H%M%SZ)-$EXPECTED_AUTHORITY_SHA"

# Seal the reviewed checkout root-only using the approved host procedure, then deploy.
sudo test "$(git -C /path/to/reviewed/ai-platform rev-parse --verify 'HEAD^{commit}')" = "$(git -C /path/to/reviewed/ai-platform rev-parse HEAD)"
sudo git -C /path/to/reviewed/ai-platform diff-index --quiet HEAD --
sudo test -z "$(git -C /path/to/reviewed/ai-platform ls-files --others --exclude-standard)"
sudo env OPENSANDBOX_GATEWAY_EXPECTED_AUTHORITY_SHA="$EXPECTED_AUTHORITY_SHA" \
  OPENSANDBOX_GATEWAY_AUTHORITY_EVIDENCE_ID="$AUTHORITY_EVIDENCE_ID" \
  deploy/opensandbox/install-s72.sh /path/to/reviewed/ai-platform
systemctl status --no-pager opensandbox-gateway.service
journalctl -u opensandbox-gateway.service --since '-5 minutes' --no-pager
```

The immutable release, unit/config/ACL rollback snapshot, manifest and atomic
snapshot descriptor are root-owned. Deployment authority state lives only under
`/var/lib/opensandbox-gateway-deploy` (`0700`, root:root); the public gateway unit
cannot access it. Writable SQLite runtime state remains separately under
`/var/lib/opensandbox-gateway`. The installer validates the archived source and
manifest, reloads and restarts both units, verifies their absolute release
working directories and source readback, and only then atomically switches
`/opt/opensandbox-gateway/current` and the root-only rollback descriptor. It
records the exact expected authority SHA and fresh-resolution evidence ID in the
immutable release and root-only deployment state, reads both back before
success, and restores both values (or their prior absence) after any failed
install. A failed automatic restore hard-fails while preserving its unique
recovery snapshot for operator inspection.

## Mandatory remote smoke gate

Before any provider switch, use a disposable test scope and the configured CA
trust to verify: TLS 1.2+, hostname failure, bad/missing auth, redirect refusal,
oversize refusal, create/get/list, exact merged attestation, endpoint route
token, startup sentinel upload/download/command, executor health and identity,
one callback, one model request, dispatch scope mismatch, runtime/network/image/
mount drift refusal, cancellation, repeated deletion, and bounded orphan
cleanup. Confirm from Docker inspect that the sandbox still has runsc,
`NetworkMode=none`, no-new-privileges, only accepted mounts, and no proxy
environment. Confirm denial audit rows/counters increase without secret values.

This smoke is a release gate, not evidence supplied by the source candidate.
If the regular-file relay cannot complete the real executor callback/model
envelope while network remains none, keep Docker as provider and stop; do not
enable sandbox egress or bypass attestation.

## Rollback

Run:

```sh
ROLLBACK_AUTHORITY_SHA=$(git -C /path/to/reviewed/ai-platform ls-remote origin refs/heads/main | awk 'NR == 1 {print $1}')
git -C /path/to/reviewed/ai-platform fetch origin main
ROLLBACK_AUTHORITY_EVIDENCE_ID="ls-remote-$(date -u +%Y%m%dT%H%M%SZ)-$ROLLBACK_AUTHORITY_SHA"
sudo env \
  OPENSANDBOX_GATEWAY_EXPECTED_AUTHORITY_SHA="$ROLLBACK_AUTHORITY_SHA" \
  OPENSANDBOX_GATEWAY_AUTHORITY_EVIDENCE_ID="$ROLLBACK_AUTHORITY_EVIDENCE_ID" \
  deploy/opensandbox/rollback-s72.sh
```

Rollback verifies the root-only descriptor, snapshot manifest, exact 40-hex
release, realpath confinement, source ownership, freshly resolved authority SHA
and authority evidence ID before mutation. The release owner must resolve and
fetch main immediately before invoking the root script; the root script has no
fetch credentials and requires the local tracking ref to equal that supplied SHA.
Only this rollback path may accept a previously recorded release, and only when
it remains an ancestor of the supplied fresh main SHA. It restores the previous
unit files, configuration, ACL, authority-SHA state,
enable/active state and release pointer exactly; a first-install rollback
restores their prior absence. For a historical release it records and reads back
the fresh rollback evidence ID alongside the deployed release SHA. It then
rechecks that OpenSandbox is active on
`127.0.0.1:8080`. It never changes ai-platform provider configuration and does
not delete containers, workspaces or SQLite runtime state. Rotate downstream
auth secrets if rollback followed suspected exposure.
