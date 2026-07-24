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

## Pinned HTTPS upstream bridge v1

OpenSandbox does not inherit the Docker provider's HTTP callback/model bases.
Its signed capability, create metadata, and attestation must all carry exactly
one bridge origin and these path shapes:

```text
OPENSANDBOX_GATEWAY_CALLBACK_BASE=https://REQUIRED_FIXED_EGRESS_HOSTNAME:18443
OPENSANDBOX_GATEWAY_OPENAI_BASE=https://REQUIRED_FIXED_EGRESS_HOSTNAME:18443/openai/v1
OPENSANDBOX_GATEWAY_ANTHROPIC_BASE=https://REQUIRED_FIXED_EGRESS_HOSTNAME:18443/anthropic
```

The matching `egress-policy.v1.json` pins all three targets to the single
private address `10.56.0.211`. The broker opens the socket to that literal IP,
does not consult DNS, sends `REQUIRED_FIXED_EGRESS_HOSTNAME` as TLS SNI, and
verifies the certificate hostname. Do not add aliases, alternate ports, path
variants, or a generic proxy target.

Use a dedicated internal CA for the 211 bridge leaf. Keep the CA private key
offline and never copy it to s72 or 211. The following is an illustrative
operator workflow; replace placeholders through the approved PKI procedure and
do not paste generated key material into logs, Git, environment variables, or
this runbook:

```sh
# On the approved offline PKI host.
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out bridge-ca.key
openssl req -x509 -new -sha256 -days 3650 -key bridge-ca.key \
  -subj '/CN=REQUIRED_AI_PLATFORM_BRIDGE_CA' -out bridge-ca.pem
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out bridge-211.key
openssl req -new -key bridge-211.key \
  -subj '/CN=REQUIRED_FIXED_EGRESS_HOSTNAME' -out bridge-211.csr
printf 'subjectAltName=DNS:REQUIRED_FIXED_EGRESS_HOSTNAME\nextendedKeyUsage=serverAuth\n' > bridge-211.ext
openssl x509 -req -sha256 -days 825 -in bridge-211.csr \
  -CA bridge-ca.pem -CAkey bridge-ca.key -CAcreateserial \
  -extfile bridge-211.ext -out bridge-211.pem
```

Provision `bridge-211.pem` plus its chain and `bridge-211.key` only into the
read-only 211 frontend mounts documented in the 211 release runbook. Provision
only the non-secret CA certificate as
`/etc/opensandbox-gateway/tls/upstream-ca.pem` on s72. The gateway builds a
dedicated client trust context from that exact file, retains hostname
verification and TLS 1.2+, and does not install the CA system-wide or fall back
to system roots.

The s72 gateway's own listener certificate is a separate server certificate.
Its SAN set must contain exactly the literal IP from
`OPENSANDBOX_GATEWAY_PUBLIC_AUTHORITY` (for example `10.56.1.72`) as an IP SAN;
a DNS SAN or CN is not accepted. The ai-platform client trust for that gateway
remains separate from the s72 upstream bridge CA.

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
5. The three egress policy URLs exactly equal the bridge v1 bases above and all
   three `expected_ips` arrays contain only `10.56.0.211`.
6. `/etc/opensandbox-gateway/tls/upstream-ca.pem` is the dedicated bridge CA
   certificate, not a system CA bundle, leaf certificate, symlink, or CA key.

Copy `gateway.env.example` and `egress-policy.v1.example.json` to their paths
under `/etc/opensandbox-gateway`, replace every `REQUIRED` marker, place TLS and
secret files at the referenced paths, then inspect them without printing secret
contents:

```sh
grep -R 'REQUIRED' /etc/opensandbox-gateway/gateway.env /etc/opensandbox-gateway/egress-policy.v1.json
stat -c '%a %U:%G %n' /etc/opensandbox-gateway/secrets/* /etc/opensandbox-gateway/tls/*
```

The first command must return no matches. Before invoking the installer, make
the configuration tree root-owned and non-symlinked. Directories are `0750`;
`gateway.env`, `egress-policy.v1.json`, `tls/fullchain.pem`, and
`tls/upstream-ca.pem` are `0640`; `tls/privkey.pem` and exactly the three files
`secrets/lifecycle-api-key`, `secrets/capability-token`, and
`secrets/record-signing-key` are `0440`. Groups may initially be `root` or
`opensandbox-gateway`; the installer normalizes the runtime copy to
`root:opensandbox-gateway` and validates the same contract after rollback.
Generate the three independent secrets without displaying them, for example:

```sh
umask 027
install -d -o root -g opensandbox-gateway -m 0750 /etc/opensandbox-gateway/{secrets,tls}
openssl rand -hex 32 | sudo tee /etc/opensandbox-gateway/secrets/lifecycle-api-key >/dev/null
openssl rand -hex 32 | sudo tee /etc/opensandbox-gateway/secrets/capability-token >/dev/null
openssl rand -hex 48 | sudo tee /etc/opensandbox-gateway/secrets/record-signing-key >/dev/null
sudo chown root:opensandbox-gateway /etc/opensandbox-gateway/secrets/*
sudo chmod 0440 /etc/opensandbox-gateway/secrets/*
```

The installer accepts only an exact
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
trust to verify: TLS 1.2+, hostname failure, wrong CA failure, wrong pinned-IP
failure, bad/missing auth, redirect refusal, non-success status propagation,
deadline refusal, oversize refusal, create/get/list, exact merged attestation, endpoint route
token, startup sentinel upload/download/command, executor health and identity,
one callback and one request through each model prefix with the exact rewritten
upstream path and preserved authorization header, dispatch scope mismatch, runtime/network/image/
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
