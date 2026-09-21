# Chat Run Lifecycle And Public Error Projection

Status: current public-projection contract and implementation map. Historical
queue/error repair narratives live in their original Git revisions and PRs.
This page does not define another Run, queue, Sandbox or SSE authority.

## Owners and protocol names

The public stream is SSE v4, defined by [SSE wire](redis-streams-sse-wire-protocol.md).
The internal callback receipt protocol is independently v2.1. Legacy internal
handler names such as `stream_open` and `final_detail` are projection details;
they do not enable a v3 browser adapter or retired `queue_update` producer.

Runs owns execution state and fixed safe terminal classification. The existing
`app/run_projection.py` compatibility surface delegates to the owning projection;
its eventual source migration must not create a second taxonomy.
`publicTerminalPresentation.ts` owns frontend presentation of approved public
codes. Live, historical and hydrated messages use the same presentation catalog
and message projection semantics.

## User-visible content and redaction

Assistant narration and final answers are content facts, independent of Tool
admission, active invocations, completion evidence and Run outcome. Tool lifecycle
state MUST NOT suppress ordinary Assistant text. A failed or cancelled Run keeps
its already accepted safe content; its outcome is presented separately.

### Ordinary-user execution presentation

Chat presents user-meaningful work state, not an executor transcript. The active
Run keeps its disclosure-safe work details expanded; terminal convergence folds
them into one summary that the user may reopen. The display contract is:

| Surface | Ordinary-user presentation | Allowed content |
| --- | --- | --- |
| Assistant answer, artifacts, permission requests, failures, and cancellation | Visible outside the work-details fold | Accepted answer text, authorized artifact labels/actions, and fixed actionable status copy |
| `commentary.delta` | Visible inside work details | Sanitized work-progress commentary from a complete tool-using Assistant turn |
| Tool lifecycle | Visible inside work details | Fixed public category label and canonical name derived from the allowlisted category (`skill`, `mcp`, `read`, `write`, `edit`, `search`, or `execute`); `skill` alone may use its sanitized, authorized v4 `display_name`; lifecycle status; and bounded duration |
| Execution, Sandbox, Todo, and subagent lifecycle | Visible inside work details | Fixed or allowlisted phase/category labels, status, bounded progress/duration, and an explicitly safe file basename when the public execution contract supplies one |
| Routine queue, context, intent, heartbeat, and model-completion metadata | Hidden from the transcript unless separately actionable | No ordinary-user card |
| Model reasoning and raw execution data | Always hidden | No `ThinkingBlock`, `thinking.*` body, prompt, command, path, query, file content, diff, Tool/MCP/Skill arguments or results, private identifier, storage key, credential, trace, or executor payload |

Final files are optional ordered parts of the Run's single assistant response.
The answer remains ordinary text. A file card appears only after the Agent
explicitly selected that file with `attach_file` and the platform validated,
copied, and persisted it as an immutable artifact. History and terminal
hydration preserve the attachment order recorded in each artifact manifest.
Neither the backend nor the frontend infers deliverables from answer text or
enumerates the workspace. Compatibility Run artifacts remain the storage owner
while the one-final-message-per-Run invariant binds them to the assistant part.
For older successful Runs, the persisted `result_json.artifacts` identifiers are
the compatibility allowlist and the old generated `输出文件` link suffix is
removed from answer text. Unclassified artifacts from failed historical Runs
remain visible for recovery; any explicit non-response `delivery_scope` is
always excluded.

`Bash`/execute and Read therefore remain visible as coarse lifecycle activities,
but their command, arguments, paths, read query, file body, stdout, stderr, and
result do not render. The same raw-data prohibition applies to Write, Edit,
Search, Skill, MCP, browser, and future tool categories; allowing every detail
except Bash and Read would bypass the public projection boundary. Opaque operation
identities may support reducer correlation but are not user-facing labels. A
sanitized `skill` `display_name` sourced from current authorized Skill metadata
is presentation authority and is retained in a dedicated public field. The same
authority applies when accepted answer text names an authorized Skill: the answer
gate substitutes the sanitized public display name, while an authorized internal
Skill without public metadata receives only the fixed Skill category label. Other
private capability identifiers remain redacted. Other Tool `display_name` values
and all subagent `display_name` values remain protocol facts but are not
presentation authority in compatibility-shaped parts; ordinary chat derives their
fixed labels from the allowlisted category and subagent kind.
Unknown categories, malformed public identities, unknown lifecycle states, or
activities without the required public identity fail closed. Current Sandbox
readiness remains visible through the v4 public execution timeline; legacy
`sandbox` parts, sandbox IDs, and raw sandbox errors are not display inputs.
Subagent cards likewise use only the public operation identity and public
lifecycle fields; raw subagent input, result, and error values never enter the
panel store or markup. Conversation-wide derived views such as the image gallery
index only accepted message content, authorized attachments, and recursively
filtered public subagent children; they never scan raw Tool or subagent payloads.

This presentation uses existing v4 public events and does not add an SSE event,
second history shape, or new execution authority. The ordinary Chat renderer no
longer renders legacy `Message.toolCalls`/`toolResults` or tool parts lacking v4
public metadata. Those fields may still deserialize while old state is read, but
they are ignored as display input; no authorized ordinary-user compatibility
consumer was identified. Existing answer, artifact, permission, and strict public
history projections remain unchanged. Regression checks inventory both legacy
raw tool-frame rejection and renderer-level absence.

Redaction replaces sensitive spans rather than dropping the containing paragraph
or message. Skill accounts, passwords, tokens and private service endpoints must
be identified from the authorized run-scoped Skill/configuration material before
that material can produce public output. Pattern matching supplements known
sensitive values; it cannot establish that arbitrary unlabelled text is safe.
Sensitive values and the replacement registry never enter public events or logs.
Cross-chunk matching retains only the undecided suffix needed for safe redaction.

Non-sensitive narration, public Tool labels and authorized task-file references
must survive redaction. They are not grounds for deleting an entire string.
Platform credentials, private execution envelopes and other principals' material
remain excluded. Authentication, tenant/workspace/session authorization, Tool
admission and browser rendering safety are unchanged by this content policy.

Live delivery, committed history and terminal hydration use the same content
policy. The frontend adapter maps metadata-only `message.completed` to a public
activity rather than a final text chunk, and builds Assistant text from accepted
`message.delta` frames. Full Assistant messages reconcile their own streamed
fragments by stable source identity. A distinct final answer must not be
discarded because it is not a prefix extension of earlier narration; absent or
ambiguous source identity fails closed instead. Repeated delivery of the same
source must not duplicate content; equal text from distinct sources is not a
duplicate. Terminal hydrate reconciles the same Run segment, preserving
intervening narration, Tool/process parts, artifacts, Todo state, permission
requests, and other actionable statuses. The hydrated fold includes accepted
narration and final answer, not only the last successful fragment.

## Queue-to-processing presentation

A truthful submission/status response may show `queued` and `queue_position`.
An accepted current-Run v4 `stream.open` clears the queue presentation after
schema, binding, incarnation, connection-generation and cursor validation.
Opening a stream proves publisher readiness, not successful Tool execution or
available global capacity. An internal mapping to `stream_open` is allowed.
A stale frame cannot dismiss the current Run's queue state. Terminal, error,
cancellation, session replacement and setup failure retain idempotent cleanup.

Queue metrics, scheduling limits and Redis facts remain supported. Do not
reintroduce the retired browser `queue_update` fallback. A disconnected browser
does not determine the Run's execution state.

## Terminal and content facts

Run outcome, transport end and final-content synchronization are distinct.
Keep accepted safe partial text while a terminal status is displayed. Terminal
hydrate reconciles the same Run segment rather than appending a second answer or
replacing unrelated sources. For a streamed Sandbox answer, the terminal carries
only a versioned `AssistantAnswerReceipt`; the Worker accepts only current-Attempt,
strictly ordered, committed v4 rows. Final answer persistence does not wait for
Redis delivery or drain publication work. `visible_to_user` and the exact
Attempt-bound committed facts govern eligibility; retired publication metadata
is not consulted. Retirement materializes suppression before removing that
metadata. Receipt, identity, sequence, count, or length mismatch fails closed. For streamed answers, short compatibility
content remains inline only when message/result persistence limits permit;
otherwise history stores a bounded `run_events_v4` reference. Legacy
non-streaming bounded terminal messages use the same stable-source
`assistant_delta` compatibility shape only when no streamed answer exists;
obsolete `assistant_final` is retired.

Public execution process parts stay expanded while the Run is active. On
terminal convergence the renderer folds completed public execution steps into
one collapsed process summary, optionally showing elapsed time only from
trusted bounded timestamps; the user can manually reopen it. Final text,
narration, artifacts, failures, cancellation, permission requests, and Todo
state remain visible outside that presentation fold.

`result_unavailable` is backend-confirmed absence of a displayable terminal
answer. `terminal_result_unavailable` is a frontend condition where a known
terminal result could not be synchronized. `status_unavailable` denotes missing
status evidence and cannot replace a known terminal failure. None authorizes
automatic resubmission of the user's task.

Exhausting fast SSE reconnect attempts while authoritative status remains active
enters bounded-rate recovery, not `status_unavailable`. A known terminal Run with
a transient history-loading failure remains eligible for result recovery without
resubmission or removal of accepted content. Recovery belongs to the current
session/Run/connection generation and is cancelled on replacement or disposal.
Authorization failure and foreign incarnation/cursor rejection remain fail-closed.

Only the backend-approved fixed code/kind/severity taxonomy selects public text.
Unknown, private, malformed or kind-mismatched details use the fixed `run_failed`
fallback. Frontend display ignores arbitrary backend message text for these
status cards. Distinguish execution-service unavailability from explicit model
upstream failure according to the owning code mapping.

Public-answer projection remains fail-closed for secrets, concrete Skill implementation/source details, structured executor or storage fields, and model Thinking content. Ordinary paths in intentional answer text are allowed as user-visible project context; pre-release thinking events may retain status compatibility but their body is not rendered. A rejected public projection is not a model or Run execution failure: the executor preserves its authoritative terminal status and omits the unsafe answer from ordinary-user projections. Historical records with the retired projection-failure code are presented as the generic fixed `run_failed` terminal state.

## Public outcome summary and pre-Run admission

Run playback includes `ai-platform.public-run-outcome.v1` as an additive
ordinary-user projection. It always answers four questions: what
happened, what completed work was durably retained, what the user can do next,
and the copyable problem number. Controlled `phase` and `detail_code` support
presentation but do not create another Run state machine.

The projection derives retained work only from authoritative Run status, the
approved public terminal taxonomy, completed database step rows and registered
artifact rows. Physical Sandbox files, private diagnostics, arbitrary exception
messages and browser-local observations are not proof that work was retained.
Consequently a delivery/projection failure can report completed work and
registered files without claiming the Run never started. An SSE or HTTP
disconnect is a transport fact: the frontend says the background Run may still
continue, keeps any accepted content, and does not automatically resubmit.

Before Session/Run persistence, Chat admission validates the current identity,
Agent Profile, model selection, attachment access, required input and the final
MCP tool set. Explicit, inherited and Profile-injected MCP selections are
resolved first and authorized once in the admission transaction. A deterministic
denial therefore creates no Run and consumes no queued execution. Worker
authorization before the actual Tool call remains mandatory for policy changes
and time-of-check/time-of-use protection. Runtime credential issuance, endpoint
and network reachability, file retrieval/content and Tool outcome remain runtime
facts; moving them into admission would require side effects or provide false
certainty.

## Change Contract: public outcomes and final capability admission

- **Owner and scope:** Runs owns `ai-platform.public-run-outcome.v1`; Chat owns
  deterministic pre-persistence admission. Existing Run/Attempt, queue, SSE,
  artifact and Worker authorization authorities remain unchanged.
- **Reached invariants:** every projected failure answers the same four user
  questions; retained claims require database facts; transport failure does not
  become Run failure; explicit, inherited and Profile-injected MCP tools pass
  one final-set authorization before Run creation.
- **Acceptance:** projection contract tests cover start/partial/delivery/file/
  permission states; frontend tests cover copyable identifiers and uncertain
  transport; Chat route tests prove denial rollback and Profile-tool admission.
- **Evidence ceiling:** local tests and browser checks do not prove external MCP
  reachability, real PostgreSQL/Redis behavior, deployment or production
  acceptance.
- **Compatibility and retirement:** the public response adds an optional field.
  The separate explicit-tool authorization pass is replaced by final-set
  admission; Worker reauthorization is retained. Rollback can stop emitting the
  optional projection and restore the prior admission call shape without data
  migration.
- **Stop conditions:** unknown status, unregistered artifacts, unsafe error text,
  ambiguous Tool outcome or an authorization storage failure must fail closed;
  they cannot be converted into a success, a retained-file claim or a blind
  retry instruction.

## Change Contract: executor terminal protocol evidence

- **Owner:** Runs owns the bounded private diagnostic record and platform-admin
  projection; the Sandbox terminal validator supplies one pre-normalization
  failure observation.
- **Bounded paths:** executor probe terminal validation, the existing lease receipt
  claim fence, existing Runs diagnostic application/domain contracts, the
  platform-admin diagnostics response and Run Monitor section, their focused
  tests, and this document.
- **Reached invariants:** Run, Attempt, lease, callback and terminal authorities
  remain unchanged; ordinary-user routes and events receive no new fields;
  platform-admin reads remain tenant-scoped. Stored evidence contains only task
  and terminal statuses, known-field types/counts/lengths, Run-ID match state,
  bounded validation issues, and the fixed canonical replacement. It never
  stores message text, answer-receipt values, additional field names, executor
  payloads, paths, prompts, commands, credentials or secrets.
- **Acceptance and regression proof:** an invalid probed terminal result records
  its bounded structural summary alongside the unchanged generic failure; a
  diagnostic-storage failure cannot replace that terminal result. Run-ID mismatch
  is classified as protocol-invalid, and only the current receipt claim may append
  evidence. The existing administrator endpoint and Run Monitor show reported,
  validation and canonical views; tests prove capture, tenant/admin authorization,
  and absence of supplied private marker values.
- **Evidence ceiling:** source and local focused tests do not prove deployed
  OpenSandbox behavior, packaged images, migrations or browser acceptance.
- **Migration and rollback:** the existing versioned `run_diagnostics` JSONB
  envelope carries the additive field, so no schema migration or compatibility
  reader is introduced. Rollback removes the additive capture/projection; older
  records remain readable because unknown diagnostic fields are already dropped.
- **Stop conditions:** stop and revise this contract if implementation requires a
  schema migration, ordinary-user exposure, weaker redaction or tenant fencing,
  a second diagnostic store, or any change to callback or terminal semantics.
- **Retirement and compatibility disposition:** no superseded production path,
  selector, documentation or configuration is in scope; the existing generic
  `executor_protocol_invalid` terminal remains active. Receipt idempotency remains
  for callbacks without a claim and retries holding the same claim; a stale probe
  claim may no longer accept another claimant's identical receipt.

## Change Contract: diagnostic isolation and source capture

- **Owner and scope:** Runs owns diagnostic isolation and persistence; existing
  admission, Worker, Sandbox and reconciliation boundaries supply bounded source
  evidence. This batch covers enqueue rejection, Worker pre-dispatch/escaped
  exceptions, typed Sandbox runtime failures, workspace collection and the
  platform-admin per-observation projection.
- **Preserved invariants:** public error code/message, Run/Attempt/lease authority,
  callback ordering, tenant authorization and the 128 KiB private budget do not
  change. Private carriers are removed before any fallible diagnostic operation.
- **Acceptance and stop conditions:** diagnostic builders, normalization, budgets,
  lock waits and writes may degrade without replacing a valid terminal result;
  cancellation and outer business-transaction failure still propagate. Stop if
  this requires a second store, ordinary-user fields or changed terminal semantics.
- **Retirement and compatibility:** the protocol-only caller transaction/catch is
  replaced by the common Runs service plus Repository savepoint and bounded local
  timeouts. The old admin summary remains as an additive response compatibility
  shape; `details.observations` is authoritative for individual evidence and
  duplicate same-identity handling is removed. No schema migration is required.
  Runtime GET maintenance, build-version projection and exports remain outside
  this batch and associated Issue #1485 stays open.

## Private diagnostics and reconciliation

Ordinary-user routes, SSE, history and status cards must never render private SDK
or executor envelopes, credentials, storage keys or private execution identities.
User-visible narration and Tool details pass through the content policy above;
private diagnostic payloads are not an alternate source of public answer text.
Fixed Run-status cards retain their approved taxonomy independently of content.
Admin diagnostics remain a separately authorized bounded projection, never an
alternate public endpoint.

The retained diagnostic boundary limits structured SDK/Tool values to 4 KiB,
exception text to 8 KiB, lightweight lifecycle facts to 128, detailed calls and
policy denials to the latest eight. Runs stores the normalized observations in a
separate, tenant-scoped `run_diagnostics` record with a 128 KiB per-Run budget;
new terminal results remove the private carrier before writing `runs.result_json`.
Preserve the first and newest useful evidence and normalize at each producer or
trust boundary. An admin role is not permission to expose secrets.

Within the SDK diagnostic contract, the first accepted failure remains the root
observation. Wrappers append bounded handling observations and must not replace
the root source, stage, exception, or code. Normalization exposes rejected,
invalid, unknown-field and truncation losses explicitly; unknown payloads are
not echoed. The retired `runner_error_code`, `runner_failure_source`, and
top-level `truncated` fields are read-only legacy inputs and are not emitted by
new producers. HTTP errors keep public text separate from a bounded private
carrier; the current client parses at most 4 KiB of an error body, recording an
explicit loss for malformed or larger bodies.

Worker failures append diagnostics only after the current Attempt fence succeeds.
A first terminal callback normally stores its fixed initial lease receipt fields
and diagnostic observation in the same transaction; an acknowledged duplicate
receipt does not add another observation or revision. Diagnostic-only failure is
contained by a savepoint with bounded local lock/statement timeouts, so the clean
business terminal may still commit without that observation. Connection,
savepoint and outer transaction failures retain existing failure/retry semantics.
Permanent reconciliation retains the receipt observation and appends a separate
classified handling observation. The authorized admin route reads this Runs-owned
record independently of Run status; absence and legacy data remain explicit.

Terminal-reconciliation failure uses the existing fixed code
`terminal_reconciliation_failed` for the owning permanent-contract failure or
exhausted retry policy. Private reasons remain private. Existing source budgets
include a 240-second work deadline under a 300-second stale-claim interval and a
narrower stop timeout; the current retry policy treats the fifth unclassified
terminal failure as terminal failure. These are implementation budgets, not
end-user latency guarantees. Before changing them, verify the exact code and
matching tests and record effective deployed configuration separately.

Claim the exact eligible receipt in a short transaction. Re-prove tenant,
workspace, user, Session, Run and Attempt before restoring context or collecting
outputs. Unverified historical handles remain quarantined. Verified failed-stop
handles remain recoverable. Later mutations compare the exact claim token.
A historical released lease with an eligible non-finalized receipt may still
need reconciliation; generic cleanup cannot bypass this receipt fence.
Release/finalize only after the owned stop outcome is proven. Do not synthesize
an artifact count of zero or discard independently authorized artifact records
when finalization fails.

Admin health exposes scoped counts, oldest pending age and SLO-breach counts;
these are observations, not identifiers or private errors. A reported historical
15-minute breach threshold is not proof that current reconciliation meets a
15-minute service objective. Runtime acceptance must measure it independently.

## Implementation and acceptance

`eventProcessor.ts`, `historyLoader.ts`, terminal hydration and
`MessagePartRenderer.tsx` must use the shared code catalog. Validate every
shipped locale/default against the backend code/kind/severity contract. Do not
invent an unshipped locale. Keep tests for unknown/malicious message text,
partial-content preservation, all approved error families, encrypted-file
password guidance, queue ownership and hydrated failure presentation.

[System acceptance](../acceptance/system-architecture-matrix.md) adds recovery,
client-state and fault-isolation scenarios. Source and local tests do not prove
SDK timing, API replicas, proxy flushes, deployed browser behavior or provider
cleanup. Those remain under [SSE acceptance](../operations/redis-streams-sse-cutover-acceptance.md)
and the release runbook. No source cleanup authorizes a weaker public projection.
