# s72 Co-located Platform Release Runbook

## Authority

s72 is the only future release and runtime target for the complete ai-platform
control plane and OpenSandbox execution plane. Co-location means one physical
host and two security domains; it does not authorize an untrusted sandbox to
join a control-plane network or mount control-plane state.

Only `tools/s72_colocation_authority.py deploy-main-commit` may mutate this
deployment. It holds one outer host lease while it installs the immutable
OpenSandbox server, invokes the existing gateway installer and repository
release authority, runs the fixed ordinary-user acceptance, then verifies exact
source, image, Compose, broker, and sandbox parity. Do not retry a failed authority
attempt, run Compose manually, restart systemd manually, or invoke a provider
switch. A failed attempt performs the recorded gateway and platform rollback and
must return to review before another lease is granted.

This runbook does not authorize any 211 access. Do not read, copy, export,
migrate, stop, or inspect 211 data, `.env`, volumes, containers, or secrets.

## Security Domains

The trusted control plane owns API, worker, frontend, PostgreSQL, Redis, MinIO,
model credentials, and the loopback broker entry. The untrusted execution plane
owns OpenSandbox-created runsc containers. The OpenSandbox lifecycle controller
is a narrow trusted execution controller: it alone receives Docker API access so
it can create and destroy runsc containers. Every executor must retain:

- runtime `runsc`, `network_mode=none`, UID/GID `1000:1000`, and
  `no-new-privileges`;
- no Docker socket, host network, control-plane named volume, or unscoped host
  bind;
- only attempt-scoped workspace/Skill mounts admitted and attested by the
  gateway;
- authenticated lifecycle and capability fetch, attempt-bound grants,
  callback token and current-attempt fencing, signed attestation, durable model
  route receipts, and the governed egress deny policy.

The host gateway broker sends only three path families through
`127.0.0.1:18043`: runtime callbacks, OpenAI-compatible model requests, and
Anthropic-compatible model requests. The broker entry is bound to host loopback,
has no secrets, exposes no datastore path, and returns 404 for every other path.

## Operator Inputs

Provision these inputs before requesting the mutation lease. Never use a
placeholder value.

- A fresh, clean coordination checkout whose `HEAD`, `origin/main`, and
  `git ls-remote origin refs/heads/main` are the same reviewed 40-character SHA.
- `/opt/ai-platform/deploy/ai-platform/.env`, owner-matched to the managed root,
  mode `0600`, with real platform/auth/model settings and immutable executor
  `repository@sha256` plus matching digest. The production SDK selection is
  exactly `WORKER_CLAUDE_AGENT_SDK_ENABLED=true`, permission mode `dontAsk`,
  allowed tools `Read,Glob,LS,Bash`, denied tools `Write,Edit,NotebookEdit`, and
  sandbox authority `opensandbox/governed`. `bypassPermissions`, a missing or
  empty denylist, fake provider, and implicit Compose defaults all fail closed.
- `/etc/opensandbox-gateway/gateway.env` and
  `egress-policy.v1.json` based on the committed s72 examples, with root-owned
  gateway TLS and lifecycle/capability/signing secret files. Pre-provision the
  stable `opensandbox-gateway` group and user with the numeric UID/GID declared
  by `gateway.env`; the authority rejects a missing or mismatched identity.
- `/etc/ai-platform/opensandbox/server.env`, root:root mode `0600`, with a
  reviewed OpenSandbox server `repository@sha256`, matching digest, a dedicated
  non-root UID/GID, and the observed Docker socket GID. The server image must be
  present locally or readable from its registry by exact digest.
- `/etc/ai-platform/opensandbox/server.toml`, root-owned and grouped to that
  dedicated GID at mode `0440`, based on `server-s72.toml.example`: lifecycle
  API key matching the platform/gateway, immutable execd image, `network_mode =
  "none"`, `secure_runtime = gvisor/runsc`, only the attempt workspace host
  prefix, and no OpenSandbox network-policy egress block.
- `/etc/ai-platform/model-secrets/openai-api-key` and
  `anthropic-auth-token`, both regular non-link files, mode `0440`, readable by
  the gateway group. These are the safe host source for brokered model calls.
- A gateway certificate whose identity matches the configured s72 lifecycle
  authority, an immutable reviewed executor image present or pullable by digest,
  adequate disk for the exact checkout/build plus rollback snapshot, and a
  Docker Compose version supporting the committed `!reset` override tag.
- A unique non-secret authority evidence id linked to the fixed-SHA approval and
  the single s72 mutation lease.
- `/etc/ai-platform/s72-smoke-accounts.json`, root:root mode `0600`, containing
  a JSON array of at least two real `tenant/label=username:password` ordinary
  accounts in at least two tenants, plus a bounded non-secret DOCX input. The
  canonical smoke does not create fixtures or clean queues/datastores directly.

If a real model credential, trusted internal identity setting, image digest,
certificate, or host fact is unavailable, stop with the exact failed gate. Do
not guess a value and do not deploy with an example placeholder.

## Read-only Preflight

Run from the fresh coordination checkout. This command performs no runtime
mutation and prints only safe projections; it never prints configuration values
or secret bytes.

```bash
sudo python tools/s72_colocation_authority.py preflight \
  --coordination-source /path/to/fresh-ai-platform \
  --commit <exact-reviewed-main-sha> \
  --release-root /opt/ai-platform/releases \
  --authority-evidence-id <fixed-sha-review-evidence-id> \
  --smoke-accounts-file /etc/ai-platform/s72-smoke-accounts.json \
  --smoke-sample-docx /etc/ai-platform/s72-smoke-input.docx
```

The result must verify exact-main source authority, managed file ownership and
mode, absence of all retired bridge keys, immutable images, the fixed loopback
egress policy, the exact SDK/sandbox activation selection, Docker/Compose >=
2.24.4, registered runsc, immutable OpenSandbox server registry/local
availability, Docker socket group identity, ports, disk, and current units. An
absent OpenSandbox unit is valid for a first deployment; an active unit must own
exactly one 127.0.0.1:8080 listener.
`mutation_performed` must be `false`.

## Canonical Deployment

After fixed-SHA review, all required GitHub checks, normal merge, and an explicit
Phase B charter, grant exactly one s72 mutation lease and run once:

```bash
sudo python tools/s72_colocation_authority.py deploy-main-commit \
  --coordination-source /path/to/fresh-ai-platform \
  --commit <exact-reviewed-main-sha> \
  --release-root /opt/ai-platform/releases \
  --authority-evidence-id <fixed-sha-review-evidence-id> \
  --smoke-accounts-file /etc/ai-platform/s72-smoke-accounts.json \
  --smoke-sample-docx /etc/ai-platform/s72-smoke-input.docx
```

The authority materializes one root-owned exact checkout, installs the
OpenSandbox controller and gateway from that checkout, deploys only the base plus
s72 colocation Compose selection, verifies platform and broker health, runs the
fixed `verify_multiuser_poc` login-mode run/queue/SDK Skill/SSE/artifact and ACL
smoke without fixture or cleanup flags, stores its full evidence root-only, then
re-verifies runtime and active-sandbox parity. Source/CI or a zero-active-sandbox
parity report is not that runtime smoke.

## Rollback

The outer authority snapshots the prior OpenSandbox unit and image/network
ownership; the gateway installer snapshots its prior units, config, ACL,
authority state, and current release. The outer authority also records the prior
platform commit. If a later stage fails, it restores the prior exact Compose
commit (or removes only the new containers without deleting volumes on a first
install), invokes the canonical gateway rollback, then restores the prior
OpenSandbox unit and removes only an image/network introduced by the failed
attempt. A newly created OpenSandbox SQLite state directory is preserved for
bounded recovery evidence rather than silently deleted. The result reports all
rollback branches without secret content.

An installer failure is handled by the installer's own EXIT trap; the outer
authority does not issue a second blind rollback. If either rollback branch
fails, preserve the reported snapshot/state, stop, and return
`S72_COLOCATED_PLATFORM_BLOCKED`. Do not retry or repair with ad hoc Compose or
systemd commands.

## Retained, Replaced, Retired

Retained:

- exact-main/fixed-SHA authority, immutable server/execd/executor images, one mutation lease, canonical
  Compose ownership, root-owned managed inputs, parity, and rollback evidence;
- runsc/gVisor, independent UID, network none, no Docker socket/host network,
  scoped mounts, authentication, capability identity/subjects, attempt fencing,
  attestation, callback receipts, model-route receipts, and deny policy.
- from the legacy 14-key OpenSandbox projection, the 11 non-topology runtime and
  security inputs: lifecycle authority, immutable executor image/digest,
  attestation path/version, capability URL/token, and both policy subjects.

Replaced:

- the 211-to-s72 callback/model route with a host-loopback `127.0.0.1:18043`
  broker entry controlled by the s72 Compose selection;
- separate gateway and platform operator actions with one outer s72 deployment
  authority and one rollback record;
- an externally preinstalled OpenSandbox service with an exact OCI-backed
  systemd unit, dedicated lifecycle network, health/parity gate, and reverse
  rollback owned by the same authority;
- 211 runtime/source mapping with root-owned exact s72 release checkouts.

Retired:

- fixed 211 host mapping, frontend host port `18443`, TLS/SNI bridge server name,
  source-IP pin, bridge certificate mounts, and operator-supplied external
  callback/OpenAI/Anthropic base keys;
- the legacy 14-key bundle as a cross-host deployment prerequisite: its three
  topology base URLs are replaced by the fixed loopback broker, while its 11
  security/runtime inputs remain required under the new authority; any 211
  release-owner provider switch and the legacy OpenSandbox overlay as a
  selectable future deployment path are also retired;
- historical 211/s72 evidence as proof of current readiness.
