# OpenSandbox Ephemeral Model Credentials

Status: accepted source contract. Runtime publication is a separate release gate.

## Decision

Production and governed OpenSandbox executors never receive long-lived OpenAI or
Anthropic credentials. The explicit `test`/`internal-test`/`bridge` profile may
forward both credentials only to its short-lived executor environment; this is
a bounded compatibility exception for the direct internal-test topology, not a
production credential path. API and Worker use the official OpenSandbox SDK
directly; the OpenSandbox Server owns lifecycle and runsc execution.

Model clients use the stateless Nginx egress entry. Its model paths include the
validated `run_id` and `attempt_id`:

- `/openai/<run_id>/<attempt_id>/v1/chat/completions`
- `/openai/<run_id>/<attempt_id>/v1/responses`
- `/anthropic/<run_id>/<attempt_id>/v1/messages`
- `/anthropic/<run_id>/<attempt_id>/v1/messages/count_tokens`

Nginx accepts only `POST`, rejects query strings, strips sandbox authorization
and API-key headers, injects `MODEL_PROXY_INTERNAL_TOKEN`, and adds the Run and
Attempt headers. The existing `model_control_plane.py` then validates the
binding, decrypts the pinned model connection, and forwards the request. The
proxy does not implement OpenSandbox lifecycle or capability admission.

Callbacks use the same stateless egress origin and are forwarded to the
existing `/api/ai/runtime/callbacks/*` routes. Callback-token validation remains
owned by the API; Nginx does not replace it or accept lifecycle operations.

## Credential Boundary

Administrators store compatible-endpoint roots and credentials in the Model
control plane. The API encrypts immutable connection revisions with
`MODEL_CONNECTION_ENCRYPTION_KEY`, validates the endpoint policy, and activates
a revision only after model synchronization succeeds. Outside the explicit
internal-test exception, plaintext credentials never appear in an OpenSandbox
request, Run, lease, queue payload, event, receipt, callback, response, or
lifecycle payload. The exception permits the two provider credentials only in
the OpenSandbox executor environment; metadata, labels, Run/lease/queue data,
events, receipts, callbacks, responses, and lifecycle payloads remain
credential-free.

Run admission pins the active connection revision and exact upstream model ID.
The internal proxy serves only queued or running Runs whose requested model
matches that admission. Missing or invalid encryption, proxy authentication,
Run binding, or connection configuration fails closed before an upstream model
connection is opened.

## Non-Goals

This decision does not merge the executor lease token, OpenSandbox lifecycle
API key, callback token, proxy token, and provider credential into one
capability. It does not change local Docker execution, deploy to s72 or another
host, or claim runtime acceptance from source and test evidence.
