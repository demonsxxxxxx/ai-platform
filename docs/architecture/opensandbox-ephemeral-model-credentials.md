# OpenSandbox Ephemeral Model Credentials

Status: accepted source contract. Runtime publication is a separate release gate.

## Decision

OpenSandbox executors never receive long-lived OpenAI or Anthropic credentials.
The controller omits provider secrets from the OpenSandbox create request. The
s72 gateway rejects legacy create requests that contain provider-secret
environment keys and installs only non-secret SDK bootstrap sentinels after the
request has crossed the trusted gateway boundary.

The localhost relay does not authorize model access. For every model request,
the host mailbox broker derives authority from the current active, signed
`LeaseRecord` and consumes one durable route receipt before forwarding the
request to the fixed internal AI Platform proxy. Admission is bound to:

- tenant, workspace, user, session, run, and attempt from the lease;
- the sandbox identifier and a globally keyed, random relay request identifier;
- provider, method, exact path, and the SHA-256 digest of the selected model;
- a 15-second admission window and a 512-request per-attempt limit.

Only `POST /chat/completions` and `POST /responses` are allowed for the OpenAI
route. Only `POST /v1/messages` and `POST /v1/messages/count_tokens` are allowed
for the Anthropic route. A query string is not part of this contract and is
rejected.

## Credential Boundary

The host broker never loads the compatible-endpoint credential. It removes any
sandbox-supplied `Authorization` or `x-api-key` header and sends only the
non-secret Run and attempt binding to the fixed internal proxy. The deployment
Nginx boundary discards the placeholder credential and authenticates to the API
with `MODEL_PROXY_INTERNAL_TOKEN`.

Administrators store one candidate compatible-endpoint root URL and API key in
the Model control plane. The API encrypts each immutable connection revision
with `MODEL_CONNECTION_ENCRYPTION_KEY`, validates the endpoint against the
public-network policy, and activates a revision only after `/v1/models`
synchronization succeeds. The plaintext key is never returned by an API or
written to a Run, lease, queue payload, event, receipt, denial, response,
callback, attestation, or lifecycle payload.

Run admission resolves only an enabled, currently discovered catalog entry and
pins the active connection revision and exact upstream model ID on the Run. The
internal proxy serves only queued or running Runs whose requested model matches
that admitted model, decrypts the pinned revision, resolves the upstream host to
validated fixed IPs, preserves TLS hostname validation, rejects redirects, and
streams the bounded response. Activating a newer revision affects new Runs only;
already-admitted Runs continue on their pinned revision.

Missing or invalid encryption, internal-proxy authentication, Run binding, or
connection configuration fails closed before an upstream model connection is
opened. The legacy deployment-level endpoint and key remain only as bootstrap
catalog compatibility until the database control plane has an active revision;
they are not the managed execution credential path.

## Replay And Failure Semantics

The existing SQLite lease store is the only replay authority. Receipt insertion
uses `BEGIN IMMEDIATE`, so concurrent uses of one request identifier have one
winner. Reuse with the same binding is rejected as replay; reuse with another
attempt, provider, path, or model is rejected as binding drift. Expired,
inactive, cross-attempt, and over-limit requests are rejected before insertion.

Once admitted, a receipt remains consumed even if the internal proxy, TLS,
timeout, cancellation, or upstream handling fails. An SDK retry must create a
fresh relay request and therefore consumes a new receipt. Request and response
bodies remain bounded, response streaming stays incremental, and public error
projection does not disclose connection URLs or credentials.

## Non-Goals

This decision does not merge the executor lease token, OpenSandbox lifecycle API
key, callback token, route receipt, and provider credential into one capability.
It does not change local Docker execution, deploy to s72 or another host, or claim runtime
acceptance from source and test evidence.
