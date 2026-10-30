# OpenSandbox Ephemeral Model Credentials

Status: accepted source contract. Runtime publication is a separate release gate.

## Change Contract: database-owned model execution

- **Owner and scope:** Execution owns the encrypted model connection, frozen Run
  model/capacity binding, runtime proxy and count compatibility. Deployment owns
  only the encryption key, internal proxy token, internal-host allowlist and the
  governed OpenSandbox topology. The administrator Models page owns the upstream
  origin, write-only credential, enabled directory, capacities, default, the
  platform display name and each model's user access list.
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
  capacities reach the executor; explicit `/v1/messages/count_tokens` requests
  validate successful upstream responses and use local fallback only on `404`;
  ordinary `/v1/messages` requests have no token-count preflight; an actual Run
  completes through the platform proxy without provider credentials in the
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
CLI's fixed per-path allowlist; `structured-outputs-2025-12-15` is accepted only
on `messages` for the installed CLI's structured subtask requests. It is not
accepted on `count_tokens`; unknown/empty beta values and duplicate protocol
headers remain rejected. Other upstream headers remain filtered. The proxy
does not implement OpenSandbox lifecycle or capability admission.

The pinned HTTP model transport uses the locked HTTPX/HTTPCore implementation
for HTTP framing, socket operations and TLS. The platform validates every DNS
answer before connecting, uses an approved IP in the request URL, and preserves
the original authority in `Host` and hostname in the TLS `sni_hostname` extension.
It supplies the existing system-trust SSL context explicitly, disables environment
proxies and redirects, and retains provider-header and credential isolation.
All 3xx responses are rejected without a second dispatch. Connection establishment
errors alone may try another approved IP; read, write, protocol and response-body
failures never replay a potentially accepted model POST.

Each operation owns its own client until the response is closed. There is no
cross-request IP-keyed pool, cookie jar or default provider credential. Such a
pool could reuse a TLS connection verified for another hostname on the same IP.
Responses are streamed as raw available bytes with no fixed-size batching,
including small chunked SSE events before the upstream response completes.
`Accept-Encoding: identity` is explicit; non-identity responses fail with the
existing `model_upstream_unavailable` transport error rather than being implicitly
decompressed or forwarded without their encoding header. This deliberately
rejects gateways that ignore the identity request.

The cumulative response-size limit applies before each chunk is forwarded:
16 MiB by default and 8192 bytes for explicit count-token responses. Completion,
read failure and closing a started body iterator close both response and client;
the stream owner also has an idempotent `close()` for an unstarted body. The
20-second ordinary and 3600-second streaming operation timeout budgets remain
explicit. They are per-operation network timeouts, not a DNS-inclusive or
whole-response deadline. This synchronous transport does not claim prompt ASGI
cancellation of a blocked thread: socket close may not wake its current read,
and the existing read timeout still bounds that wait.

Execution explicitly transfers stream ownership from opening worker to caller.
The handoff states are pending, delivered, accepted and abandoned: cancellation
before delivery leaves late-result close to the original worker; cancellation
after delivery closes the unaccepted result in the caller. Its lock only chooses
the owner and is never held during network I/O or close. No new executor or
fire-and-forget cleanup task is introduced. Finite catalog/count requests already
close their response in the worker and retain their existing behavior.

RuntimeProxyResponse carries the owned body's close callback through the HTTP
transport. A model-specific StreamingResponse finally runs it across the entire
ASGI invocation, including failures before body iteration or during downstream
send. Construction failures also close the accepted stream. Cleanup is synchronous
without cancellation checkpoints; secondary failures record only a fixed private
reason and never replace the primary error or cancellation.

The owned body separates resource close from iterator close. An active reader
retains iterator-close duty until its current next returns or raises, avoiding
concurrent generator.close; close suppresses a late chunk from that read. An idle
iterator closes immediately, including the Anthropic observation wrapper. Resource
close is attempted even if iterator or diagnostic finalization fails. Under the
current ASGI2.3/threadpool path, disconnect can still wait for a blocking read
before the response finally runs. This change provides deterministic ownership
and eventual cleanup, not instantaneous upstream cancellation. Request-scoped
async I/O would be a separate bounded follow-up if faster interruption is needed.

The private `http.client` connection factory and pinned HTTPS socket subclass are
retired. Their test doubles are replaced by HTTPX transport fixtures and real
local HTTP/TLS wire tests. The synchronous ModelUpstream port, catalog parsing,
Run/Attempt binding, count compatibility and private response evidence remain
owned by the platform; no provider SDK or duplicate fallback transport remains.

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

The compaction window and output cap are frozen into Run admission and
ExecutionSpec v3. For Claude, the window configures SDK-owned automatic
compaction; the model proxy does not recount `/v1/messages` requests or enforce
a separate input limit, and the engine may compact earlier than the window.
Explicit `/v1/messages/count_tokens` requests from Claude Code remain
Run/Attempt-bound proxy traffic and retain their existing endpoint validation.
The proxy still validates the requested output against the frozen output limit.

Callbacks use the same stateless egress origin and are forwarded to the
existing `/api/ai/runtime/callbacks/*` routes. Callback-token validation remains
owned by the API; Nginx does not replace it or accept lifecycle operations.

## Credential Boundary

Administrators store compatible-endpoint roots and credentials in the Model
control plane. Model discovery validates the endpoint and reads `/v1/models`
without changing the active revision. Administrative origin normalization is pure:
it is used for saved-key same-origin comparison, not as SSRF approval. The existing
offloaded HTTP request resolves DNS once, checks every returned address, and pins
the actual connection to those approved IPs. Publication's database/advisory-lock
transaction begins only after that request finishes. A canceled discovery cannot
publish; the existing finite-response worker still owns and closes its request.
OS resolver duration and default-executor occupancy are not bounded by the HTTP
read timeout, and cancellation does not instantly stop synchronous DNS.

Endpoint ports must be 1–65535. An explicit port 0 is invalid rather than an alias
for the scheme's default port; the prior truthiness-based coercion is retired.
Malformed URL/port parsing returns `model_connection_endpoint_invalid`, without
propagating parser-specific details. Unicode and ASCII IDNA hostname spellings
retain their existing normalization behavior; this change does not introduce IDNA
alias matching for saved credentials. Missing credentials or encryption setup can
now fail before DNS policy checks; every actual request must still pass those
checks before credentials are sent.

Publication re-discovers the same model
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
credential. A revision conflict retains the draft and requires an explicit reload
of the saved configuration; other publication failures retain the draft for retry. Cancellation restores the
last saved baseline. Explicit reload clears entered credentials and replaces edits
with the saved state when loading succeeds.

Disabling a model preserves its token-capacity draft. Available models keep both
capacity fields editable while disabled: administrators may retain a valid pair
or explicitly clear both fields. Before publication, the editor checks every
available row against the server contract: enabled models require both limits;
disabled models allow both absent or both integers from 1 through 10,000,000.
Incomplete or invalid pairs block the request with model-specific correction
instructions, without clearing other draft edits or the entered credential.
The server remains the final validation authority; disabling is not a validation
bypass and does not silently discard saved capacities.

The model editor's compare-and-swap baseline changes only after loading saved
configuration or receiving a successful publication. Discovery cannot advance it:
if another administrator has published since that baseline, discovery preserves
all unsaved fields, reports the conflict, and disables publication until the user
explicitly reloads. Same-revision discovery may refresh candidate membership while
retaining edits. A connection test reports connectivity only and never acknowledges
a newer catalog revision. Abandoned discovery responses cannot alter this state.

The environment does not publish a model directory. `DEFAULT_MODEL_ID` and
`MODEL_CATALOG_JSON` have no supported Settings or executor-forwarding surface.
The public `default_model_id` response remains part of the database projection.
SDK/adapter defaults (`OPENAI_MODEL`, `ANTHROPIC_MODEL`, `CLAUDE_AGENT_MODEL`)
remain supported at their existing adapter boundaries; they cannot substitute
for governed Run admission. The OpenSandbox environment sanitizer still rejects
legacy catalog payloads defensively because they may contain credentials.

Run admission pins the active connection revision, exact upstream model ID,
and input/output capacities. Existing Runs and Attempts retain that immutable
snapshot after later publication. The internal proxy serves only queued or running Runs whose requested model
matches that admission. Missing or invalid encryption, proxy authentication,
Run binding, or connection configuration fails closed before an upstream model
connection is opened.

## Per-model user access

Execution owns a per-model user list on the same catalog entry as the platform
display name. Access is a property of each model, not of the shared connection:
the gateway remains one platform-wide origin and credential.

`access_mode='all'` is the default and the meaning of an unconfigured model, so
history keeps its original availability. `access_mode='selected'` requires a
non-empty employee work-ID list. An empty selection is rejected
(`model_user_access_requires_users`) rather than read as "everyone", and an
unknown mode or malformed list is `model_user_access_invalid`. The historical
`allowed_user_ids` JSON field stores company `workid`, never DingTalk `userId`
or department IDs. Identity owns the company employee directory and validates
only newly added work IDs; local `users` records are not an employee inventory
and employees need not have logged into AI Platform before being granted access.
Carried-over members are not revalidated, so saved restrictions remain editable
and members can be removed during a directory outage. New grants fail closed
with a retryable directory error; there is no local-user fallback.

The administrator-only `GET /api/ai/admin/company-employees` endpoint projects
only `work_id` and `display_name`. It uses the deployment's trusted tenant scope,
not a client-selected tenant, and reads the existing same-origin
`GET /api/DingTalk/getUserDetail` array through Identity. Only `正式员工` and
`试用期` are eligible; blank/unknown/`离职` status is not. Blank or malformed
work IDs are excluded. Exact duplicate identities collapse; conflicting
work-ID identities or one DingTalk user ID mapped to multiple work IDs are
excluded. Server-side literal name/work-ID search and pagination (100 per page,
50,000 records, 16 MiB response bound) cover the whole upstream response.
Employee selection reuses the existing department directory's address resolution:
an empty `DINGTALK_DEPARTMENT_AUTHORITY_BASE_URL` uses the existing company
DingTalkAPI origin `http://10.56.0.25:5033`; a configured value overrides that
origin for both read-only directories. No additional deployment configuration is
required. The adapter reuses `DINGTALK_DEPARTMENT_AUTHORITY_TIMEOUT_SECONDS`
(default 5 seconds, maximum 30). Directory failures still return 503 with no
local-user fallback. AD login and department-membership authority retain their
existing configuration and fail-closed behavior; their scope is not expanded
by the read-only directory default.

Malformed/oversized responses and upstream failures return a safe 503; private
phone/email/union ID fields are never projected or logged.

Directory I/O runs outside database transactions. Publication retains its
existing revision compare-and-swap, while omitted access fields inherit the
saved catalog values. Model selectors use this employee API, not the separate
local-user diagnostics API. The replaced `existing_tenant_user_ids` query and
Postgres repository lookup callback are removed; diagnostics remain for their
existing operational consumers. AD authentication and trusted Chat principal
construction are unchanged.

The public catalog and new-Run admission both filter by the calling principal
inside their own SQL, so the default model, an explicit `model_id`, and a raw
upstream `model_value` share one predicate and none of them can bypass the list.
Administrators manage the list but receive no usage exemption. The public
projection reports `default_model_id` from the already-filtered rows, falling
back to the first visible model and reporting no default when the caller can use
none; the per-model user list itself never appears in a user projection.

Revocation applies to admission, not to work already admitted. An existing Run
or Attempt keeps its frozen model, capacities and revision, and its proxy
requests continue to succeed against that snapshot. A new child Run created by
copy, retry or resume is admitted only if the current list still includes the
caller; the check runs inside the child-Run transaction before queue admission,
so a rejection rolls the child back and never leaves an unauthorized queued Run.
A child whose model row no longer exists in the catalog is also refused.

An update that omits `access_mode` and `allowed_user_ids` keeps the saved
restriction, so an older client cannot silently widen a restricted model. An
omitted `display_name` keeps the saved platform name; an explicitly empty value
clears back to the upstream model ID. Neither field changes the model ID,
upstream value, provider association or the forwarded request.

## Non-Goals

This decision does not merge the executor lease token, OpenSandbox lifecycle
API key, callback token, proxy token, and provider credential into one
capability. It does not change local Docker execution, deploy to s72 or another
host, or claim runtime acceptance from source and test evidence.
