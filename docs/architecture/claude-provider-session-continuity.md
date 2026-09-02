# Claude Provider Session Continuity PRD

Status: implementation and static review complete; Draft PR, CI, and candidate acceptance pending
Owner: Context + Execution
Design baseline: `origin/main@7956c98b`
Last updated: 2026-09-02

## 1. Problem

Each platform Run currently starts an isolated Claude Agent SDK session. The
platform reconstructs recent ordinary conversation from PostgreSQL and embeds
it in the executor-private `context_pack`. The SDK's local transcript is scoped
to one Run and disappears with the sandbox.

This preserves ordinary conversation, but loses provider-native continuation:
tool-call lineage, SDK compaction state, subagent transcripts, and the exact
Claude transcript needed by `resume` do not survive an ephemeral sandbox.

Claude Agent SDK 0.2.130 provides `SessionStore`, `session_id`, `resume`, and
`session_store_flush="eager"`. The platform must use that native contract
without making the sandbox filesystem durable or exposing provider transcripts
through public context projections.

## 2. Product outcome

For Claude executions in one owned platform Session:

1. the first execution starts one stable provider session with `session_id`;
2. transcript entries are mirrored through an authenticated host callback and
   committed to PostgreSQL;
3. a later sandbox loads the same opaque transcript and invokes Claude with
   `resume`;
4. a confirmed native transcript replaces reconstructed recent conversation in
   that Claude prompt, while platform Messages and immutable context snapshots
   remain authoritative for UI, audit, authorization, and cross-engine fallback;
5. files, artifacts, current Agent Profile, current Skill/tool authorization,
   and current system policy continue to come from the current Run;
6. a missing required transcript, cross-scope access, competing writer, or
   mirror failure fails closed instead of silently starting fresh or reporting
   reliable success.

This phase implements Claude only. Pi continuation is explicitly deferred.

## 3. Change Contract

### Owner

- Context owns provider-session binding, opaque transcript persistence, prompt
  conversation selection, limits, and retention inheritance. Its application
  layer exposes narrow callback and transcript-state use cases; PostgreSQL is
  composed only in bootstrap.
- Execution owns provider identity dispatch, public per-Run projection, resume
  evidence validation, the Claude `SessionStore` adapter, and SDK option selection.
- Runtime callback transport owns authenticated sandbox-to-host delivery only.

### Bounded paths

- `app/context/**`
- `app/execution/**`
- composition in `app/bootstrap/context.py` and `app/bootstrap/execution.py`
- `app/executors/claude_agent_sdk_runner.py`
- `app/runtime/sandbox/contracts.py`, `executor_app.py`, and `runtime.py`
- `app/routes/runtime_callbacks.py`
- `app/session_continuity.py`
- composition-only edits in the frozen `app/worker.py` and
  `app/executors/claude_agent_worker.py`; neither file may grow
- `app/schema.sql` and `app/schema_migrations.py`
- focused tests under `tests/`
- this document, `docs/architecture/agent-conversation-context.md`, and
  `docs/README.md`

No frontend, public API, streaming protocol, tool-policy, deployment, or Pi
runtime changes are in scope.

### Reached invariants

1. **Exact scope:** every load/append is authorized by the current
   run-attempt callback capability and active sandbox lease. Tenant, workspace,
   user, Session, Agent, and Run scope come from locked PostgreSQL authority,
   not transcript payload fields.
2. **Stable identity:** one active Claude provider UUID is bound to one exact
   platform Session and context epoch. The sandbox-supplied UUID must match the
   host-derived UUID.
3. **One writer:** one run attempt owns provider-session append authority at a
   time. A second active attempt fails closed; terminal or inactive ownership
   may be replaced under the binding lock.
4. **Opaque round trip:** transcript entry JSON is stored and loaded without
   interpretation or public projection. Main and subagent transcripts preserve
   append order. Entries carrying an SDK UUID are idempotent.
5. **Fresh versus resume:** no stored main transcript means `session_id`; an
   existing main transcript means `resume`. A resume load may never degrade to
   a fresh session.
6. **No duplicated conversation:** while no provider transcript exists, the
   existing executor-private recent conversation bootstraps Claude. Once a
   main transcript exists, reconstructed recent conversation is omitted from
   that Claude context pack; non-conversation context remains current.
7. **Current capabilities:** resume never restores historical tools,
   credentials, Agent Profile, Skill Set, or authorization. Those are rebuilt
   from the current Run.
8. **Commit receipt:** callback success is returned only after the PostgreSQL
   transaction commits. A success-like SDK terminal requires at least one
   acknowledged eager append in that execution; zero-append and mirror-error
   outcomes fail closed.
9. **Non-disclosure:** provider transcript entries, provider UUIDs, callback
   credentials, subpaths, and storage details remain executor-private and are
   absent from public events, Runs projections, logs, and error messages.
10. **Bounded input:** append batch count, serialized batch bytes, subpath
    length, entry shape, and loaded transcript bytes are bounded before use.
11. **Retention:** provider rows inherit the parent Session lifecycle and are
    physically removed by database cascade when that Session is purged. This
    phase adds no independent public retention or deletion surface.
12. **Workspace independence:** no sandbox, home directory, or local Claude
    config path becomes durable.

### Acceptance

- First Claude Run uses `session_id`, an eager `SessionStore`, and persists the
  main transcript through the host callback.
- A later Run in the same platform Session loads committed entries and uses
  `resume` in a new sandbox/workspace.
- A different tenant, workspace, user, platform Session, Run, attempt, callback
  token, provider UUID, or inactive lease cannot read or append entries.
- Subagent `subpath` entries round-trip and are enumerable for SDK resume.
- Concurrent active attempts cannot both append to one provider session.
- Duplicate SDK entry UUIDs do not create duplicate rows; entries without UUID
  remain append-only.
- A missing expected resume transcript, a success-like terminal with no
  committed eager append, and `MirrorErrorMessage` each produce a private
  stable executor error and do not terminalize the Run as succeeded.
- Current platform recent conversation remains present for bootstrap and is
  absent after native continuation exists; current files/artifacts/memory and
  current policy remain present.
- Focused unit, route, schema, worker-adapter, sandbox-executor, and installed
  SDK contract checks pass through the repository local test-stage runner.
- Architecture governance reports no new frozen-hot-file growth.

### Falsifiable regression proof

At minimum, tests must fail if any of these regressions occur:

- provider identity returns to per-Run UUIDs;
- `session_id` and `resume` are passed together;
- a missing resume silently starts fresh;
- append responds before commit or skips active-attempt authorization;
- transcript scope can be selected by sandbox-provided tenant/user fields;
- provider conversation and reconstructed conversation are both delivered on
  resume;
- mirror errors or a success-like zero-append terminal are accepted;
- two active attempts obtain writer ownership;
- transcript data appears in a public projection.

### Evidence ceiling

Local tests and static checks prove implementation behavior only. They do not
prove packaged-image behavior, PostgreSQL behavior on s72, cross-sandbox cold
resume, or release acceptance. Those claims require the exact candidate image
in an isolated Docker-capable acceptance lane.

### Rollback

Disable use of the Claude `SessionStore` and restore per-Run SDK session IDs;
platform recent-conversation reconstruction remains available as the fallback.
The additive provider tables may remain unread or be removed in a later
migration after retention review. Rollback must not delete transcript rows
ad hoc.

### Stop conditions

Stop and revise this contract before proceeding if implementation requires:

- making provider transcript content public;
- trusting sandbox-supplied scope rather than locked Run authority;
- mounting PostgreSQL credentials or host storage into the sandbox;
- allowing concurrent writers for one provider session;
- changing public SSE, callback-receipt, or Run protocols;
- reconstructing or interpreting Claude's opaque transcript schema;
- modifying Pi continuation; or
- expanding a frozen hot file with new owned logic.

## 4. Architecture

```text
PostgreSQL
  platform Session
    └─ Claude provider binding (provider UUID, context epoch, writer attempt)
         └─ ordered opaque entries (main transcript + subpaths)

Worker context materialization
  ├─ no committed main transcript -> keep recent platform conversation
  └─ committed main transcript    -> omit reconstructed conversation

Ephemeral sandbox
  PlatformClaudeSessionStore
    ├─ load/list_subkeys -> authenticated runtime callback -> PostgreSQL
    └─ append            -> authenticated runtime callback -> PostgreSQL commit

Claude Agent SDK
  ├─ first use: session_id=<stable provider UUID>
  ├─ resume:    resume=<same provider UUID>
  ├─ session_store=<adapter>
  └─ session_store_flush="eager"
```

The SDK `project_key` is not an authorization or storage key. It is derived
from sandbox `cwd`, which changes between attempts. The adapter does not expose
it to the host. Host lookup is bound to authoritative platform scope and the
stable provider UUID; only `subpath` remains part of the provider transcript
key.

## 5. Data model

### `provider_session_bindings`

One current binding per `(tenant_id, session_id, engine)`:

- exact tenant/workspace/user/session/agent scope;
- `engine = 'claude'`;
- stable `provider_session_id` UUID;
- `context_epoch` (starts at 1);
- `next_sequence` for ordered append;
- current writer `run_id` and `attempt_id`;
- timestamps.

The row is created only by an exact scoped `insert ... select` from the active
parent Session. Provider UUID derivation is repeated by the host and compared
with the request.

### `provider_session_entries`

- binding scope and provider UUID;
- normalized `subpath` (`NULL`/empty storage key means main transcript);
- monotonically assigned sequence;
- optional SDK entry UUID;
- opaque JSONB entry;
- timestamp.

A partial unique index deduplicates non-empty SDK entry UUIDs within one
provider transcript/subpath. Main and subpath loads order by sequence.

## 6. Runtime behavior

### Bootstrap

1. Worker materializes the immutable context snapshot.
2. Context persistence ensures/loads the Claude binding.
3. With no committed main transcript, existing recent conversation stays in
   the executor-private pack.
4. The worker derives the stable provider UUID from platform Session identity.
5. The sandbox adapter loads the host store, acquires writer ownership, and
   confirms the main transcript is absent.
6. Runner passes `session_id`, `session_store`, and eager flush.

### Resume

1. Context materialization observes a committed main transcript and emits an
   empty reconstructed conversation while preserving other context.
2. The sandbox adapter loads the entries through the exact attempt callback.
3. Runner passes `resume` only. SDK materializes the returned transcript and
   loads enumerated subpaths.
4. New entries append through the same callback.

### Failure

- callback authentication/scope/lease/writer conflict: fail closed;
- required resume load missing/empty: fail closed, never switch to `session_id`;
- batch or loaded transcript exceeds limits: fail closed;
- `MirrorErrorMessage`: return a stable private executor failure;
- local sandbox transcript remains disposable and is not evidence of host
  durability.

## 7. Delivery tasks and live status

| ID | Module | Owner | Status | Proof |
|---|---|---|---|---|
| M0 | PRD, Change Contract, baseline and flow inventory | Parent | complete | this document |
| M1 | Context domain rules, PostgreSQL binding/entry persistence, schema migration, bootstrap/resume conversation selection | Agent Context | complete | focused domain, SQL-fencing, and conversation-context checks implemented; canonical gate pending |
| M2 | Authenticated host callback endpoints and Execution-owned `SessionStore` adapter | Agent Boundary | complete | callback isolation, writer fencing, bounded transport, narrow Context use cases, and bootstrap composition implemented; canonical gate pending |
| M3 | Stable platform-Session provider identity, Claude SDK new/resume option selection, eager flush and mirror-error failure | Agent Execution | complete | runner + installed SDK contract checks implemented; canonical gate pending |
| M4 | Composition updates, schema migration, documentation/index, focused verification | Parent | complete with evidence ceiling | compileall, focused Ruff, and `git diff --check` pass; the governed test-stage is blocked before pytest by the recorded Windows process-wrapper `PermissionError` |
| R1 | Independent staged correctness/security/architecture review | Review agents | complete | three final reviewers found no remaining correctness, isolation, architecture, or migration issues; no pytest-pass or runtime claim is made |

Module writers run sequentially in one isolated worktree. Each handoff must
report changed files, tests run, unresolved risks, and whether the PRD status
still matches implementation. The parent updates this table immediately after
each accepted module.

## 8. Deferred

- Pi session persistence or a universal provider transcript schema;
- public transcript browsing, export, deletion, or operator UI;
- provider-session fork/reset and context-epoch rotation APIs;
- independent transcript TTL policy;
- transcript compaction beyond Claude SDK's native behavior;
- preserving sandbox-local Claude files;
- deployment, migration execution, or s72 acceptance in this task.
