# Claude Run interaction and completion

The additive input protocol uses schema version `2026.10.07.1`.

Runs owns durable supplementary text and question answers. Execution owns their
translation into the current Claude session. These inputs retain the current
Run/Attempt, Profile, model, tools and files; they do not create another Run.

Supplementary text is queued while the current SDK query executes and sent with
the public `ClaudeSDKClient.query` at its next result boundary. The initial
streaming input remains open until the Run has no accepted supplementary text.
Every model Result reconciles its permission-denial receipts before a subsequent
input continues the Run, including results from background turns. Model questions use native `AskUserQuestion`; its PreToolUse hook awaits an
answer and supplies the answers through `updatedInput`. The successful native
PostToolUse hook acknowledges consumption; UI submission alone does not mark
an answer applied. Cancellation interrupts the SDK and releases input waiters.
PreToolUse is the single question entry; there is no second question handler in
`can_use_tool`.

## Public HTTP contract

- `GET /runs/{run_id}/inputs` returns
  `{run_id, state, inputs, questions}` for the authenticated Run owner.
- `GET /sessions/{session_id}/run-inputs?limit=20&before_run_id=...` returns
  `{session_id, runs, has_more, next_before_run_id}` for the authenticated session
  owner. Runs are ordered by `(created_at, id)` descending with an exclusive
  cursor in the same tenant/user/session. A foreign or absent cursor yields an
  empty page. Each item uses the same Run projection below.
- `POST /runs/{run_id}/inputs` accepts either
  `{input_id, text}` or `{input_id, question_id, answers}`. `input_id` is a
  client-generated UUID retained across retries. The response is
  `{input_id, status}`. Accepted text is `queued`, then `applied` after the SDK
  accepts its query. A conflicting reuse of an input ID returns 409.
- `state` is `open`, `sealed`, or `inactive`. Submission to a closed, terminal,
  cancelling or non-current Attempt returns 409 `run_input_closed`.
- Input rows contain `input_id`, `kind` (`text` or `answer`), `text`,
  `question_id`, `answers`, `status` (`queued`, `applied`, or `closed`), `created_at`.
  Unapplied inputs project as `closed` after their Attempt closes; persisted
  receipts remain queued/applied.
- Question rows contain `question_id`, `questions`, `status` (`pending`,
  `answered`, `resolved`, or `closed`), `created_at`. Each question has
  `key`, `question`, `header`, `options: [{key, label, description}]`, `multiSelect`.
  Question keys (`q0`…`q3`) and per-question option keys (`o0`…`o7`) are stable
  ordinals inside the batch. Answers map question keys to a selected option key,
  an array of keys for multi-select, or `{text: ...}` for free text. Free text is
  explicit so a literal `o0` cannot be mistaken for an option selection.
  Labels are display text and may collide after redaction. The SDK adapter keeps
  original question/option identity only in the active attempt and translates
  accepted keys back to native `AskUserQuestion` values. Raw model-generated
  question text is neither persisted nor sent through the callback.

No new SSE event is required. Chat queries this projection while the Run is
active. A separate session/auth-bound history projection restores every loaded
Run page on refresh and supports loading older pages; replacing the active Run
retains prior rounds as read-only history. The active Run overrides its history
snapshot by Run ID. Session or principal replacement aborts old reads and drops
old projections. Persisted pre-key records use a display-only legacy mapping;
new submissions require the current key contract. It displays queued
text separately from persisted conversation Messages and shows pending questions
as answerable cards. Text and answers are Run input facts; the existing provider
coverage continues to count the original conversation user message and final
assistant message. SDK SessionStore persists all native continuation/tool entries.

## Executor callback contract

`POST /runtime/callbacks/inputs` uses the existing callback token header and
`run_id`, `attempt_id`, `callback_token_id` scope. It accepts one operation:

- `open`: establish the current Attempt's input session before SDK execution.
- `question`: publish `{question_id, questions}` idempotently.
- `poll`: return unapplied input commands; optionally filter by `question_id`.
- `ack`: mark the supplied `input_ids` applied; resolve their answered questions.
- `settle`: return the earliest unapplied text command, or atomically seal input
  admission when none remain. It must serialize against user submission on the
  owning Run so an accepted input cannot disappear into a closing SDK session.

All responses contain `{state, inputs}`. Commands returned by poll are repeated
until acknowledged. A response lost after SDK query acceptance retries only the
ACK, never the query. Pending questions survive a browser reload. Old Attempts
cannot publish, poll, acknowledge or seal the current input session.

Text is bounded to 16,000 characters; a question batch has at most four questions
and eight options per question. The public projection uses the existing user-input
path/Skill-marker and secret redaction policy and typed fields, never raw tool arguments.
Question labels removed by that policy receive safe numbered placeholders; ordinal
keys still identify the original choice within the active SDK attempt. Native raw
question identity does not enter the new input tables or public callback projection;
existing provider transcript persistence retains its own contract. Ordinary-user text and
free answers are sanitized before persistence/execution as initial input already
was. The current principal's existing administrator exemption applies to text
and free answers; the authenticated callback receives that admitted value.
Ordinary-user reads still reapply public redaction. Selection identities carry
only ordinals, regardless of the submitting role. The table is Run-owned and
cascades with Run deletion. The native question hook uses the Run deadline,
or a one-day ceiling when execution has no configured deadline.

## Completion boundary

The SDK's public receive stream closes only after its reader's final transcript
flush. Consume that EOF before freezing the final provider sequence. Track errors
and activity in the platform SessionStore and platform-injected callbacks.
At EOF, seal callback entry, cancel active platform callbacks and join their
finalizers before publishing completion. This tracks platform work, not every
SDK child task. A confirmed retry clears only its own unresolved store failure.
Physical `disconnect()` cleanup remains after completion and is owned by the
Executor lifespan. Do not replace the SDK transport or inspect `_child_tasks`.
Stop and timeout interrupt the SDK, close the input stream and drain public
EOF before terminality, including pending SessionStore batches. Repeated callback
sealing sends cancellation only once, so asynchronous finalizers remain intact.
Only teardown after this barrier transfers to the cleanup owner. If the protocol
cannot reach EOF within the existing cleanup deadline, public disconnect is the
supported fallback; the pinned SDK combines its final mirror flush with teardown,
so this exceptional path must await disconnect. If the CLI emits
an error Result then exits nonzero, retain that structured error and drain the
remaining public stream through the final flush; success followed by an
unexpected transport error still fails.

The pinned SDK may drop a MirrorError notification when its receive buffer is
full. Until a reliable public mirror-error hook exists, a narrowly isolated,
version-tested error observer may remain; it must not take ownership of SDK
task scheduling, transport replacement, or transcript materialization.

## Required verification

Cover same-Run queued continuation, multiple inputs, question answer/reload,
idempotent submission, late submission versus sealing, stale Attempt, owner
scope, cancellation while awaiting an answer, tail mirror failure, callback
cancellation finalizers, and slow transport cleanup. Use the installed SDK with
synthetic transport for lifecycle tests; source and controlled-provider tests
are separate from production acceptance. The receive/flush ordering is checked
against the pinned SDK; actual provider and deployment latency still require
runtime acceptance.

Deploy additive schema version `2026.10.07.1` before the API and Executor. Older
binaries do not implement the input protocol; deploy the matching frontend,
API and Executor together.

Retire the transport-replacement/SDK-child-task close shim and one-shot-only
Chat submission restriction. Existing Run cancellation, SessionStore native
resume, SSE v4 and ordinary tool authorization remain their existing owners.
