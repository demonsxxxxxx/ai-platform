# OpenSandbox Ephemeral Model Credentials

Status: accepted source contract. Runtime publication is a separate release gate.

## Change Contract: database-owned model execution

- **Owner and scope:** Execution owns the encrypted model connection, frozen Run
  model/capacity binding, runtime proxy and count compatibility. Deployment owns
  only the encryption key, internal proxy token, internal-host allowlist and the
  governed OpenSandbox topology. The administrator Models page owns the upstream
  origin, write-only credential, enabled directory, capacities and default.
- **Bounded paths:** model-control-plane and OpenSandbox credential application
  logic and tests, the internal-test OpenSandbox Compose overlay, package assembly,
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
- **Retirement and compatibility:** direct provider-credential forwarding remains
  removed. The restored test-only ordinary-bridge package uses the same
  database-owned platform model proxy and Run/Attempt-bound short-lived
  capability, with a host-published proxy port reachable only on the intended
  private interface. Its leases carry exact test-profile identity rather than
  a governed network proof. Production signed leases and historical test leases
  retain their own exact cleanup checks. Rollback uses a prior immutable package
  and retained configuration after activity is drained.

## Decision

All OpenSandbox executors receive only a Run/Attempt-bound model proxy
capability, never long-lived OpenAI or Anthropic credentials. The runtime reaches
the proxy through the host-published private test bridge endpoint for
internal-test; the retained production topology uses its dedicated task
bridge with host guard. The ordinary test bridge does not deny private,
link-local, metadata, host or peer destinations. API and Worker use the official
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

The pinned HTTP model transport forwards available response bytes incrementally,
including small chunked SSE events before the upstream response completes. The
64 KiB read size is a maximum per read, not a minimum batching threshold. The
cumulative response-size limit still applies before each chunk is forwarded;
completion, read failure, and closing the started body iterator close both the
response and its connection. Existing socket timeout budgets remain unchanged;
this transport change does not introduce retries after streaming starts.

For successful Anthropic `/v1/messages` SSE responses, the proxy emits private
`model_wire_text` checkpoints scoped to Run, Attempt and one model call. Each
checkpoint records the count of `text_delta` events, character length and
SHA-256 of their concatenated UTF-8 text, never request data, raw response
bytes, reasoning or response text. Checkpoints are emitted during streaming,
so interrupted Runs retain partial evidence. `final=true` marks the last
observation of that response; `complete=true` requires `message_stop` and full
diagnostic coverage, not merely HTTP EOF. Samples have both flags false.
These logs are not public Run events, SessionStore entries or terminal facts.

Compare `model_wire_text` with the private `executor_sdk_text_checkpoint`
Run event for the same Run, Attempt and `call_ref`: both sides derive this
32-hex opaque reference from the provider `message_start.message.id` using
HMAC-SHA256 with a Run/Attempt-derived scope. The scope is an existing private
binding, not a new secret or deployment prerequisite. Raw message IDs are
never logged; references cannot be correlated across Attempts or Runs. They
are private diagnostic fields, not metric labels or public message identities.
Missing identities remain unbound (`call_ref=null`, incomplete coverage).
`events`, `chars` and SHA-256 cover raw `text_delta` events within that single
response, including tool-before text and subagent text, before answer
reconciliation and public projection. Each new response resets the digest.
The SDK also observes frames drained after an interrupt through public EOF;
that tail contributes only to private checkpoints, never to further public output.
Both sides sample at events 1, 128, 256, 512 and subsequent powers of two,
and emit a final observation on `message_stop` or early stream closure.
The Sandbox queues these immutable snapshots and attaches them to the next
authorized callback without changing their counts when answers coalesce.
Remaining diagnostics are flushed through that same private callback channel
on SDK exit; failure of this best-effort flush preserves errors/cancellation.
The queue retains at most 64 snapshots and the SDK tracks at most 64 active
parent scopes; overflow or unacknowledged delivery can leave partial evidence.
The API validates the fixed digest/count/reference/lifecycle payload and
persists it under the existing callback receipt, with `visible_to_user=false`; it does not
project it to public v4 events or SSE. An unacknowledged batch, cancellation,
missing completion, missing call identity, or the diagnostic size limit
can leave only partial evidence. SDK callbacks and model logs must be queried
with administrative access; neither records raw text.

For a single model response, compare identical `(Run, Attempt, call_ref,
events, chars)` checkpoints and their digest; use `final`, `complete` and
`coverage` to distinguish samples, complete responses and interrupted prefixes.
Do not compare a per-response digest with concatenated text across calls.
Matching model and SDK digests show the model text reached the SDK unchanged
at that checkpoint; a mismatch localizes a difference to the
proxy-to-SDK path. A mismatch between SDK and public text implicates SDK answer
reconciliation or platform projection, not necessarily the SDK package alone.
Compare only verified common prefixes. Public text can contain separators,
sanitization and terminal supplements; private checkpoints are not a public
answer transcript or proof of Run completion. Other provider paths and
non-SSE responses have no
model-side evidence. Historical Runs predating these checkpoints cannot be
retroactively attributed.

Model capacities are frozen into Run admission and ExecutionSpec v2. For Claude,
the input capacity configures the SDK-owned automatic-compaction window; the
model proxy does not recount `/v1/messages` requests or enforce a separate input
limit. Explicit `/v1/messages/count_tokens` requests from Claude Code remain
Run/Attempt-bound proxy traffic and retain their existing endpoint validation.
The proxy still validates the requested output against the frozen output limit.

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

The administrator page keeps editable connection and model drafts separate from
the last saved state. Loading configuration reports it as untested. Testing uses
the current draft and does not change candidates; discovery replaces the candidate
directory while preserving edits for matching model identities. Neither probe
rewrites the draft endpoint or saves configuration. Editing the endpoint clears
an entered credential; a saved credential can be reused only for the same origin.
Connection edits, cancellation, reload, permission loss and unmount abort owned
requests and invalidate stale callbacks. Saving locks the draft until the atomic
publication returns, then adopts the acknowledged revision and clears the entered
credential. A revision conflict retains the draft and requires fresh discovery;
other publication failures retain the draft for retry. Cancellation restores the
last saved baseline. Explicit reload clears entered credentials and replaces edits
with the saved state when loading succeeds.

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
