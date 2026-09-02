# Agent Conversation Context PRD

## Purpose

Agent conversation continuity is a platform responsibility. Every admitted run
must receive the authorized same-conversation text needed to interpret the
current request without asking the model to retrieve the immediately preceding
assistant answer.

This document owns the product requirements for selecting stored conversation
messages, materializing executor-private text, applying a bounded context
budget, and adapting that context to an Engine. It does not own message
persistence, file bytes, model-loop internals, or current-run capability
admission.

## Problem

Before provider-session continuity, the path stored immutable context snapshots
but projected recent conversation messages through a public-safe manifest before
execution. A prior message that exceeds a per-message inline limit is reduced to
an identifier and `requires_retrieval`. The previous Claude Agent SDK path
received no message body unless the model elected to call
`read_session_messages`.

Claude bootstrap now receives bounded reconstructed recent conversation through
the executor-private context pack. After a committed provider transcript exists,
later Claude turns use native `SessionStore` resume. Platform Messages and
immutable context snapshots remain the audit and fallback authorities.

That previous behavior breaks ordinary follow-ups such as `A`, `continue`, or
`use the second option`: the current request may depend on choices in the latest
assistant answer, while only an older user question remains inline. A snapshot
that proves an omitted message was authorized does not make that message
available to the model.

## Product Decision

Basic conversation history uses an ordered, executor-private message sequence.
The platform materializes authorized message bodies before dispatch, groups
messages into complete user turns, and removes the oldest complete turns only
when the total context budget requires it.

Message retrieval remains an overflow and inspection capability for older
history. It is not the normal continuity mechanism for the most recent
conversation turn.

The semantic reference is the FastGPT approach at commit
`8800099a77a93a46f2a66254298bc5f33168b1a4`: stored chat items become standard
model messages, and context limits remove old complete user turns while
preserving system context and the newest turn. FastGPT source is comparative
input, not a runtime dependency or implementation authority.

## Ubiquitous Language

**Authorized Conversation Message**
: An immutable stored user or assistant message selected by the exact run-bound
  context snapshot and proven to belong to the same tenant, workspace, user,
  session, and conversation authority as the run.

**Conversation Turn**
: One user message followed by every assistant message that belongs to that user
  request. Tool history may join the turn only through an executor adapter that
  can preserve a complete, safe tool call/response pair without restoring
  current-run capability.

**Executor Conversation Context**
: The ordered, bounded, executor-private user/assistant text actually supplied
  to the Engine adapter. It is not an ordinary-user projection.

**Context Receipt**
: The immutable run binding that records which messages were authorized and the
  deterministic selection contract. It is evidence of executor input only when
  those exact authorized bodies are materialized under the same binding.

**Public Context Summary**
: A safe projection of counts, sources, trimming state, and versions. It never
  substitutes for executor-private conversation text.

## Required Behavior

### 1. Source and authorization

1. Stored `messages` rows remain the conversation text authority.
2. The immutable run context snapshot remains the authorization boundary for
   selecting messages. It records at most 64 prior candidate message IDs plus
   the current-run message IDs; this is an authorization ceiling, not a
   model-facing count or turn selector.
3. Materialization must fetch only message identifiers selected by that exact
   snapshot and must re-prove tenant, workspace, user, session, and run binding.
4. A missing, duplicate, or out-of-scope materialization fails closed before
   Engine dispatch. Returned rows are normalized to durable conversation order
   and repository return order is not an authority.
5. Retry of the same run uses the same ordered selected message set and the same
   deterministic trimming algorithm.

### 2. Canonical message sequence

1. Executor context represents messages as ordered records with `message_id`,
   `run_id`, `role`, and full authorized `content`.
2. Only user and assistant conversation text enters the first implementation.
3. Historical system messages never enter the sequence. The admitted current
   Agent Profile supplies the only system instructions.
4. Historical tool calls do not restore Skill, MCP, Bash, or other capability.
5. Files, artifacts, and memory remain independently authorized references and
   do not become implicit conversation text.

### 3. Complete-turn selection

1. Messages are ordered by the durable conversation order `(created_at, id)`.
2. A user message starts a turn; following assistant messages remain in that
   turn until the next user message.
3. Selection evaluates turns newest first and renders selected turns in
   chronological order.
4. The current user request is supplied exactly once through the current-run
   prompt boundary and is not duplicated as history.
5. The latest complete prior turn is retained whenever one exists.
6. When the context budget is exceeded, the oldest complete turn is removed.
7. The selector never retains a user message while dropping its corresponding
   assistant answer merely because that answer is longer than a per-message
   threshold.
8. There is no fixed 640-character or equivalent per-message omission rule for
   ordinary conversation history.

### 4. Context budget

1. One total conversation-history budget replaces independent per-message
   inline limits.
2. The budget accounts for role framing and UTF-8 content with a deterministic,
   conservative estimator until a target-model tokenizer is an owned platform
   dependency.
3. System instructions, current request, file metadata, Skill catalog, and
   required output reserve are budgeted outside or before historical turns.
4. A latest prior turn that exceeds the historical budget remains available as
   one complete turn; the Engine adapter may reject an assembled request that
   exceeds the model's hard context limit rather than silently deleting half of
   the turn.
5. Future context checkpoints may replace older removed turns only through a
   separately versioned, testable summarization contract. They are not required
   for the initial cutover.

### 5. Engine adaptation

1. Engine-neutral conversation records terminate at the Engine adapter.
2. An adapter that accepts native message arrays receives role-preserving
   messages.
3. The Claude Agent SDK adapter uses bounded reconstructed conversation during
   bootstrap. After a committed provider transcript exists, later Claude turns
   use native `SessionStore` resume; platform Messages and immutable context
   snapshots remain the audit and fallback authorities.
4. The transcript distinguishes historical data from system instructions and
   the current request.
5. The model is not instructed to call `read_session_messages` to understand the
   latest prior turn.

### 6. Projection and observability

1. Executor-private message text must not enter ordinary-user context summaries,
   operational logs, or public events.
2. Public projections may report candidate, snapshot-authorized, and
   candidate-omitted message counts, context version, and whether older
   retrieval remains available. The context manifest contains no current
   request, historical message body, message ID, or per-message selection
   result.
3. Runtime evidence may identify message and turn counts but must not expose raw
   message identifiers or content to ordinary users.
4. Context assembly failures use bounded error categories without echoing text.

## Removed Behavior

The conversation execution path must stop relying on:

- conversation fields in the context manifest, including `current_message`,
  `recent_messages`, `inline_content`, message summaries, message token
  estimates, and message `requires_retrieval` rows;
- the fixed eight-message snapshot clamp;
- per-message character limits that erase an otherwise affordable turn;
- message reference identifiers rendered into the model prompt;
- `Context pack: N message(s)` as a substitute for the selected text;
- model-initiated `read_session_messages` as the recovery path for recent
  conversation history;
- process-local SDK session state as cross-run conversation authority; committed
  Claude provider transcripts are used only for native continuation. Platform
  Messages and immutable context snapshots remain audit and fallback authorities.

The retrieval API itself remains for explicitly older history and authorized
inspection. File, artifact, and memory manifest behavior is not removed by this
cutover.

## Acceptance Criteria

1. Given a prior assistant message longer than 640 characters whose final text
   defines options A and B, when the next user message is `A`, the exact A/B
   choice text is present in the executor conversation context.
2. That scenario requires no `read_session_messages` invocation.
3. Given three complete turns and a budget for two, the oldest turn is absent
   and both newer user/assistant pairs remain complete and ordered.
4. Given one latest prior turn larger than the historical budget, that turn is
   retained whole and no older turn is retained.
5. Current user text appears exactly once in the final Claude prompt.
6. Historical system messages and historical capability authorization never
   enter executor conversation context.
7. A snapshot-selected message from another scope or a materialization with a
   missing selected identifier fails before SDK dispatch.
8. New context manifests contain no conversation fields or message identifiers;
   legacy persisted manifest fields are discarded at the sanitization boundary
   and only their non-sensitive count may migrate to the new public projection.
9. Existing file, artifact, memory, public provenance, run isolation, and
   current-run capability tests remain green.

## Delivery Boundaries

The initial delivery includes the PRD, an executor-private message
materialization seam, complete-turn selection, Claude transcript rendering, and
focused regression tests.

It excludes schema migrations, UI changes, checkpoint summarization, changes to
message retention, file content inlining, and deployment. Runtime claims require
a later controlled-host acceptance run
of the exact deployed subject.

## Rollback

The source change is rollbackable by reverting the implementation and PRD index
entry. No data rewrite or schema rollback is required. Existing messages and
context snapshots remain readable through a one-way sanitization migration;
new snapshots never emit the removed conversation-manifest fields.
