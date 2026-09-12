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

Public-answer projection remains fail-closed for secrets, concrete Skill implementation/source details, and structured executor or storage fields. Ordinary paths in natural-language answer and thinking text are allowed as user-visible project context. A rejected public projection is not a model or Run execution failure: the executor preserves its authoritative terminal status and omits the unsafe answer from ordinary-user projections. Historical records with the retired projection-failure code are presented as the generic fixed `run_failed` terminal state.

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
policy denials to the latest eight, and the aggregate block to 128 KiB within
the 256 KiB Run result bound. Preserve newest useful evidence and normalize at
each producer/trust boundary. An admin role is not permission to expose secrets.

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
