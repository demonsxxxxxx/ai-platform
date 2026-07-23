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
2. The public DNS name resolves to s72, the certificate SAN covers that exact
   name, the complete chain validates, and only the TLS gateway port will be
   reachable from ai-platform.
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

The first command must return no matches. Deploy from the exact reviewed source
checkout:

```sh
sudo deploy/opensandbox/install-s72.sh /path/to/reviewed/ai-platform
systemctl status --no-pager opensandbox-gateway.service
journalctl -u opensandbox-gateway.service --since '-5 minutes' --no-pager
```

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
sudo deploy/opensandbox/rollback-s72.sh
```

This disables and removes only the gateway unit and its workspace ACL. It
leaves OpenSandbox on loopback, verifies that service is active, and does not
touch ai-platform provider configuration. Source, policy, secrets, and SQLite
state remain for forensic recovery. No container, workspace, or state data is
deleted. Rotate the two downstream auth secrets if rollback followed any
suspected exposure.
