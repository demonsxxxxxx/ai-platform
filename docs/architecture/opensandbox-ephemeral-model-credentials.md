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
`LeaseRecord` and consumes one durable route receipt before opening the pinned
HTTPS connection. Admission is bound to:

- tenant, workspace, user, session, run, and attempt from the lease;
- the sandbox identifier and a globally keyed, random relay request identifier;
- provider, method, exact path, and the SHA-256 digest of the selected model;
- a 15-second admission window and a 512-request per-attempt limit.

Only `POST /chat/completions` and `POST /responses` are allowed for the OpenAI
route. Only `POST /v1/messages` and `POST /v1/messages/count_tokens` are allowed
for the Anthropic route. A query string is not part of this contract and is
rejected.

## Credential Boundary

The host broker loads the two provider credentials from named files. It removes
any sandbox-supplied `Authorization` or `x-api-key` header, then injects
`Authorization: Bearer ...` for OpenAI or `x-api-key: ...` for Anthropic. The
credential is never written to the lease, receipt, denial, response, callback,
attestation, or lifecycle payload.

Missing or invalid provider-secret configuration fails closed before a model
connection is opened. Rotation takes effect when the gateway process restarts
and reloads the files; the SQLite receipt table is retained across that restart.

## Replay And Failure Semantics

The existing SQLite lease store is the only replay authority. Receipt insertion
uses `BEGIN IMMEDIATE`, so concurrent uses of one request identifier have one
winner. Reuse with the same binding is rejected as replay; reuse with another
attempt, provider, path, or model is rejected as binding drift. Expired,
inactive, cross-attempt, and over-limit requests are rejected before insertion.

Once admitted, a receipt remains consumed even if TLS, timeout, cancellation,
or upstream handling fails. An SDK retry must create a fresh relay request and
therefore consumes a new receipt. Existing request and response body handling,
stream flags, timeout selection, cancellation, and public error projection are
unchanged.

## Non-Goals

This decision does not merge the executor lease token, OpenSandbox lifecycle API
key, callback token, route receipt, and provider credential into one capability.
It does not change local Docker execution, deploy s72 or 211, or claim runtime
acceptance from source and test evidence.
