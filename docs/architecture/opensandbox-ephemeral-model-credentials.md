# OpenSandbox Ephemeral Model Credentials

Status: accepted source contract. Runtime publication is a separate release gate.

## Change Contract: database-owned model execution

- **Owner and scope:** Execution owns the encrypted model connection, frozen Run
  model/capacity binding, runtime proxy and count compatibility. Deployment owns
  only the encryption key, internal proxy token, internal-host allowlist and the
  governed OpenSandbox topology. The administrator Models page owns the upstream
  origin, write-only credential, enabled directory, capacities and default.
- **Bounded paths:** model-control-plane and OpenSandbox credential application
  logic and tests, both released OpenSandbox Compose overlays, package assembly,
  this contract, and the existing model administration component and mounted test.
- **Preserved invariants:** Run/Attempt and capability binding, endpoint
  validation and DNS pinning, write-only provider credentials, frozen model and
  capacity selection, request/header allowlists, and the internal proxy token
  remain fail closed. A count endpoint response other than explicit `404`, or a
  malformed successful count response, never enables local fallback.
- **Acceptance:** API discovery/publication succeeds with an allowed compatible
  endpoint; Worker can decrypt the same pinned revision; selected model and
  capacities reach the executor; both explicit count requests and message
  preflight work when that endpoint lacks `/v1/messages/count_tokens`; an actual
  Run completes through the platform proxy without provider credentials in the
  sandbox environment.
- **Evidence limits and stop conditions:** source and focused tests do not prove
  host firewall, OpenSandbox network attachment, upstream correctness or model
  output. Stop deployment if active work exists, the selected package's proxy
  topology is unavailable, the immutable image is unqualified, a non-404 count
  error would be masked, or any secret would enter source, logs or evidence.
- **Retirement and compatibility:** the internal-test direct provider-credential
  forwarding setting, Compose values, executor branch and supporting assertions
  are removed. Both released OpenSandbox profiles now use the database-owned
  platform proxy. Internal-test retains its ordinary `bridge` network and a
  Docker-bridge-bound proxy port, so it remains test-only and cannot provide
  production network-isolation acceptance. Existing internal-test environments
  must set `MODEL_CONNECTION_ENCRYPTION_KEY`, `MODEL_PROXY_INTERNAL_TOKEN`,
  `OPENSANDBOX_EGRESS_PROXY_URL` and
  `OPENSANDBOX_EGRESS_PROXY_BIND_ADDRESS`; they retain
  `MODEL_CONNECTION_ALLOWED_INTERNAL_HOSTS` for private upstreams and the
  existing `SANDBOX_CALLBACK_TOKEN`. Rollback uses the prior immutable package
  and configuration after activity is drained.

## Decision

All OpenSandbox executors receive only a Run/Attempt-bound model proxy
capability, never long-lived OpenAI or Anthropic credentials. Production reaches
the proxy on its isolated internal network. The explicit
`test`/`internal-test`/`bridge` profile reaches the same proxy through a port
bound only to the host's Docker bridge address; it remains a test topology and
does not claim production network isolation. API and Worker use the official
OpenSandbox SDK directly; the OpenSandbox Server owns lifecycle and runsc
execution.

Model clients use the stateless Nginx egress entry. Its model paths include the
validated `run_id` and `attempt_id`:

- `/openai/<run_id>/<attempt_id>/v1/chat/completions`
- `/openai/<run_id>/<attempt_id>/v1/responses`
- `/anthropic/<run_id>/<attempt_id>/v1/messages`
- `/anthropic/<run_id>/<attempt_id>/v1/messages/count_tokens`

Nginx accepts only `POST`, rejects all OpenAI query strings, and accepts only
empty or exact `beta=true` on the two Anthropic paths. It strips sandbox
authorization and API-key headers, injects `MODEL_PROXY_INTERNAL_TOKEN`, and
adds the Run and Attempt headers. The existing `model_control_plane.py` then
validates the binding, decrypts the pinned model connection, and forwards the
request. Anthropic version and beta headers are restricted to the installed
CLI's fixed per-path allowlist; other upstream headers remain filtered. The
proxy does not implement OpenSandbox lifecycle or capability admission.

Model capacities are frozen into Run admission and ExecutionSpec v2. Every
Anthropic messages request validates its requested output against the frozen
output limit and uses the pinned upstream `/v1/messages/count_tokens` result for
the input limit. If and only if that endpoint returns `404`, the platform uses a
conservative local estimate equal to the canonical count-request UTF-8 byte
length plus fixed protocol overhead; explicit SDK count requests receive the
same estimate. Authentication, authorization, rate-limit, server, transport and
malformed-success failures still fail closed. Native-resume and
platform-bootstrap overflows retain their separate current error behavior.

Callbacks use the same stateless egress origin and are forwarded to the
existing `/api/ai/runtime/callbacks/*` routes. Callback-token validation remains
owned by the API; Nginx does not replace it or accept lifecycle operations.

## Credential Boundary

Administrators store compatible-endpoint roots and credentials in the Model
control plane. Model discovery validates the endpoint and reads `/v1/models`
without changing the active revision. Publication re-discovers the same model
identities and atomically activates a new encrypted connection revision together
with the enabled directory, configured token capacities, and one default model.
Users fetch the new public directory when they reload chat; no live catalog push
or polling is required. Plaintext provider credentials never appear in an
OpenSandbox request, environment, Run, lease, queue payload, metadata, event,
receipt, callback, response, or lifecycle payload.

Run admission pins the active connection revision, exact upstream model ID,
and input/output capacities. Existing Runs and Attempts retain that immutable
snapshot after later publication. The internal proxy serves only queued or running Runs whose requested model
matches that admission. Missing or invalid encryption, proxy authentication,
Run binding, or connection configuration fails closed before an upstream model
connection is opened.

## Non-Goals

This decision does not merge the executor lease token, OpenSandbox lifecycle
API key, callback token, proxy token, and provider credential into one
capability. It does not change local Docker execution, deploy to s72 or another
host, or claim runtime acceptance from source and test evidence.
