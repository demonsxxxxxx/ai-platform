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
request. Anthropic version and beta headers pass protocol validation; beta
names are not enumerated or scoped by operation. Other upstream header names
remain filtered. The proxy does not implement OpenSandbox lifecycle or
capability admission.

### Model request byte budget

The OpenAI model routes and Anthropic messages/count-token routes accept at most
**104,857,600 serialized request-body bytes (100 MiB)**, inclusive. The Nginx model
locations use `client_max_body_size 100M` and the API counts cumulative ASGI body chunks
against the same boundary before extending its buffer. A request exceeding it by
one byte receives HTTP `413` before model dispatch. The API keeps the existing
`model_proxy_request_too_large` error code. Nginx `100M` uses binary units, not
100,000,000 decimal bytes. This default aligns with Dify’s Nginx default and
Open WebUI’s deployment example, replacing the initial 32,000,000-byte proposal.
It is independent of token windows, automatic compaction and upstream request
limits; an upstream may still reject an admitted request.

The former 1 MiB model-body limit rejected valid long tool conversations even
after SDK compaction. It is replaced in both layers, without a legacy toggle.
The server-level 1 MiB budget remains for callbacks and other locations. Internal
tokens, Run/Attempt capabilities, pinned model/output capacity, request/header
validation, DNS pinning and external-effect receipt/retry protections are
unchanged. No request is retried or task replayed by this budget change.

Model locations use `proxy_request_buffering off` with upstream HTTP/1.1, so
Content-Length and chunked model bodies stream to the API without requiring the
whole request in Nginx's retained 16 MiB `/var/cache/nginx` tmpfs. Callback request
buffering and that tmpfs size remain unchanged. Nginx may begin forwarding before
an oversized chunked body's total is known; API cumulative admission still rejects
it before model dispatch. No partially forwarded model POST is retried by this
change.

The larger budget permits proportionally more buffered API request memory per
concurrent request. It remains finite; it does not promise that every 1M-token or
multimodal input fits. Change acceptance covers a
valid UTF-8 JSON body above the old limit, exactly 104,857,600 bytes and cumulative
chunk overflow, both provider route families and count requests, retained invalid
capability rejection, and Nginx/API budget agreement without expanding callbacks.

Retirement and compatibility disposition: the API's model-body `1024 * 1024`
condition and model-route inheritance of Nginx's `1m` limit are replaced. The
existing overflow selector is migrated to the new boundary; no old-budget switch
or alternate implementation remains. Existing clients and stored Runs need no
migration, and the `413` machine code remains compatible. The owning inventory is
`rg -n 'client_max_body_size|MODEL_PROXY_MAX_REQUEST_BYTES|model_proxy_request_too_large'
app/execution/transport/model_management.py deploy/ai-platform/opensandbox-egress-nginx.conf.template
tests/test_model_management.py tests/test_runtime_launch_script.py`.
Rolling out only one layer does not activate the larger end-to-end budget;
rollback to either old layer restores 1 MiB admission for subsequent requests.
Verification must exercise exact-boundary and chunked bodies with the 16 MiB
Nginx tmpfs used by both OpenSandbox Compose profiles. Source tests do not
establish deployed configuration. Runtime replacement follows
the release procedure and must preserve active tasks and existing copied files.

### Anthropic beta protocol boundary

Execution validates the wire form of `anthropic-beta`, not a list of known
feature names. Both `v1/messages` and `v1/messages/count_tokens` accept unknown,
well-formed beta tokens and pass them to the configured upstream. The platform
has no separate requirement to prohibit named experimental model features;
duplicating the SDK's feature directory here blocked supported ToolSearch
requests when the CLI changed its gateway defaults. The name allowlist and
operation-specific beta sets are therefore retired rather than expanded.

The current qualification target remains SDK `0.2.174` / CLI `2.1.310`. CLI
versions from `2.1.304` enable tool search through an `ANTHROPIC_BASE_URL`
gateway by default ([SDK tool-search documentation](https://code.claude.com/docs/en/agent-sdk/tool-search)).
Selected-MCP activation adds `ToolSearch` after successful discovery. New beta
names from this path no longer require a platform policy update. This permits
new upstream feature behavior; locking the SDK and exercising its real requests
remain necessary, and forwarding a token does not prove upstream support.

The retained protocol checks are:

- `anthropic-version` must be exactly `2023-06-01`.
- Duplicate `anthropic-version` or `anthropic-beta` header fields are rejected.
- An absent beta header is allowed. A present field must be nonempty and at
  most 4096 bytes, with at most 16 comma-separated items before deduplication.
- Each item is 1–128 ASCII HTTP token characters (RFC 9110 `tchar`); only
  surrounding SP/HTAB is trimmed. Empty items, other whitespace, control
  characters, non-ASCII data and HTTP separators are rejected.
- Accepted items retain their case and spelling and are forwarded sorted and
  deduplicated. No date suffix, feature-name pattern or per-operation set is
  required. Rejected input is neither stripped nor retried.

Internal-token and Run/Attempt capability checks, allowed methods/paths/query,
frozen model binding, capacity, credential replacement, endpoint security and
MCP per-call authorization remain unchanged. An upstream rejection is not a
local protocol rejection. Messages preserve upstream HTTP status and body;
count-token errors retain their existing bounded error mapping (for example,
400 becomes `model_proxy_count_tokens_failed` / HTTP 502). Only an explicit
count-token 404 permits the existing local count fallback. No feature-stripping
retry or alternate model dispatch is introduced.

Installed-CLI MCP regressions run discovery, ToolSearch and synthetic tool calls
against a loopback model using the production `_runtime_proxy_headers` validator.
They cover explicit/default search, ordinary/enterprise-gateway registration
and compatible model aliases. Model-proxy regressions also send a synthetic
future token through both HTTP operations and the real upstream adapter, verify
credential isolation, and prove upstream 400 gets one dispatch, not a retry.
Malformed, oversized, empty and over-count fields remain negative controls.
No new dependency updater, runtime toggle or CI lane is introduced.

Private rejection evidence retains Run/Attempt correlation and protocol policy
revision 2. `beta_rejection` is `too_large`, `too_many`, `empty` or
`invalid_format`; `beta_count` is present only when items were inspected.
`rejected_beta_count` counts distinct invalid/empty items and is zero when the
size/count guard stops before item validation. Raw beta/header values are never
retained in logs, diagnostics or ordinary-user errors.

Retirement and compatibility disposition: `_ANTHROPIC_BETA_ALLOWLIST` and its
messages-only/count-token name-rejection assertions are removed, with no runtime
compatibility switch or fallback. The existing error code
`model_proxy_anthropic_beta_not_allowed` remains the protocol rejection code so
current transport, public projections and historical Run readers stay compatible.
Kernel diagnostic normalization retains revision-1 `not_allowed` as read-only
compatibility for persisted evidence until that diagnostics schema is retired;
new protocol validation never emits it. The owning tests explicitly cover both
historical and revision-2 facts. The fixed allowlist table and maintenance
instructions are replaced by this boundary. SDK upgrades still run the installed
CLI regressions, but do not add beta names to a platform registry.

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
