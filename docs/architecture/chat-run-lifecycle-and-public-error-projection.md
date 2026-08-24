# Chat Run Lifecycle And Public Error Projection

## Purpose

This document records the repository-wide audit and disposition for two
ordinary-user Chat projections:

1. the queued-to-processing transition after Run submission; and
2. disclosure-safe terminal error presentation.

It is an implementation map, not a second Run, queue, Streaming, or error
classification authority. The detailed SSE v3 wire contract remains in
`redis-streams-sse-wire-protocol.md`. Run lifecycle and runtime ownership remain
in `run-lifecycle-boundary.md` and `runtime-authorities.md`.

The audit used source commit
`4dfc6dbf6aa3c00a1b346359983da97a6d7d4413` as its fixed starting point and
searched `app/`, `frontend/`, `tests/`, `tools/`, `scripts/`, `docs/`, and
`schemas/`. Dependency, build, cache, and generated-output directories were
excluded. Historical release evidence was searched for obsolete assumptions but
was not treated as current runtime evidence.

This record spans companion delivery PRs #1183 and #1184. PR #1183 owns the
queue changes described below; PR #1184 owns public terminal presentation and
this audit record. Until #1183 is merged, the queue sections describe its
reviewed candidate state rather than the source tree at PR #1184's isolated
head. PR #1184 must follow #1183, or first incorporate the exact #1183 changes,
before this document can be treated as the current combined repository state.

## Authority Map

| Concern | Authority | Public projection |
| --- | --- | --- |
| Queue admission, position, dequeue, and claim | Redis queue, Run repository, worker | Submit/status APIs may truthfully return `queued` and `queue_position`. |
| Run execution start | Run/worker lifecycle | An accepted SSE v3 `stream_open` tells the current browser Run that its stream publisher is active. |
| SSE transport and replay | SSE v3 schema, Redis Stream, shared Streaming runtime | The frontend v3 adapter validates and maps frames before Chat state changes. |
| Internal execution failure | Executor, parser, repository, reconciler | Private. Raw codes, messages, exceptions, paths, commands, and identities are not ordinary-user content. |
| Public terminal classification | `app/run_projection.py` | Fixed public detail code, kind, severity, and allowlisted message. Unknown/private input becomes fixed `run_failed`. |
| Chat terminal presentation | `publicTerminalPresentation.ts` | Fixed frontend title/detail selected by public detail code; backend-provided message text is ignored. |
| Terminal recovery | exact-Run terminal hydration and history reconstruction | Reuses the same `final_detail` processor and presentation catalog. |

## Queue Lifecycle

### Audited Target Flow (Issue #1182 / PR #1183)

1. Submission may return `queued` with a current queue position. `useAgent.ts`
   creates the single indefinite `chat-queue` toast for that accepted Run.
2. The worker admits the Run and the Streaming runtime publishes a schema-valid
   SSE v3 `stream_open`.
3. `publicRunStreamV3.ts` validates schema, run ID, incarnation, header cursor,
   payload shape, and design ID, then maps the frame to the internal
   `stream_open` event.
4. `eventHandlers.ts` applies binding, session, Run, generation, and cursor
   fences before any side effect. An accepted current-Run `stream_open`
   dismisses `chat-queue`, shows the existing fixed `queueStart` notification,
   commits only the transport cursor, and does not mutate messages.
5. Terminal, error, cancellation, session change, and setup-failure cleanup keep
   their existing ownership and remain idempotent.

`stream_open` is a browser transition signal, not a replacement queue metric. It
does not claim that all execution work has started or that capacity is
available globally.

### Queue Retirement Delivered By PR #1183

PR #1183 removes the browser `queue_update` compatibility event from the
internal event union and handler. The fixed-base audit found no current SSE v3
schema, backend publisher, generated public type, tool, script, or active test
that could emit it. Keeping the handler made a real queued toast depend on an
unreachable transition.

The following are not legacy and were retained:

- API `queue_position` on truthful queued submission/status responses;
- Redis queue and Run state;
- administrative queue counts, capacity statistics, and operational tooling;
- `chat.queued` and `chat.queueStart` fixed product text;
- terminal/error/cancel queue-toast cleanup; and
- callback-receipt v2.1, which names a separate persisted protocol rather than a
  browser SSE fallback.

## Public Terminal Errors

### Classification And Non-Disclosure

`app/run_projection.py` is the sole ordinary-user terminal classification
authority. It owns the public detail-code allowlist, raw-to-public aliases, and
fixed messages. Producers may retain richer internal diagnostics, but routes,
SSE, history, hydration, and the browser must not render raw `error_message`,
exception text, parser output, storage paths, commands, tool or server names,
credentials, tenant scope, or principal data.

Known safe details pass through Chat public projection as `final_detail`.
Unknown, malformed, kind-mismatched, or private details fail closed to the fixed
`run_failed` path. Partial assistant text already admitted by the public
projection is preserved when a terminal status card is added.

### Frontend Presentation

`publicTerminalPresentation.ts` is the single frontend presentation catalog. It
contains every backend-approved public terminal detail code, the
backend-confirmed `result_unavailable` code, and the one explicitly dormant
`terminal_reconciliation_failed` presentation required by accepted ADR 0011.
The latter has no current `app/` producer; permanent reconciliation-failure
classification remains owned outside this change and is not implemented here.
Each entry fixes:

- expected detail kind;
- localized message key and source-owned default message;
- localized event-title key and source-owned default title;
- presentation stage; and
- warning or error severity.

`eventProcessor.ts` uses this catalog for live, replayed, and hydrated
`final_detail` events and ignores any backend-supplied `message` field.
`MessagePartRenderer.tsx` uses the same catalog for visible title and detail, so
a recognized code cannot fall through to `executionUpdate / warning`.
`historyLoader.ts` and exact terminal hydration continue to reuse the same event
processor rather than defining separate maps.

The Chinese locale is `frontend/web/src/i18n/locales/zh.json`. There is no
English locale file; source-owned defaults are therefore part of the executable
presentation contract and are kept semantically equal to the Chinese entries.

`result_unavailable` and `terminal_result_unavailable` are deliberately
different:

- `result_unavailable` is a backend-confirmed terminal result with no displayable
  answer; and
- `terminal_result_unavailable` is a frontend hydration condition in which the
  terminal state is known but its result is temporarily unavailable.

`status_unavailable` is likewise retained as a transport/status recovery notice,
not as a replacement for a known terminal failure.

### Public Code Families

The audited catalog covers:

- Run outcome: failed, timeout, budget exhausted, cancelled, no displayable
  result, and the ADR-required dormant reconciliation-failure presentation.
- Service and policy: model, execution, or dependency unavailable; capability or
  tool authorization failure; tool-evidence mismatch; required capability
  unavailable; and Skill sandbox admission failure.
- File handling: size, PDF password, other password protection, unsafe content,
  page or processing limits, invalid/corrupt file, encoding or type mismatch,
  identity/integrity mismatch, access loss, name conflict, storage or staging
  failure, parser-contract failure, and preprocessing failure.

For example, `context_file_pdf_password_required` renders the fixed action
`PDF 文件需要密码。请先解除密码保护后重新上传。` in live and reconstructed Chat while
discarding arbitrary backend message text.

### Retired Error Logic

The audit retired these frontend dependencies:

- the incomplete terminal presentation map embedded in `eventProcessor.ts`;
- renderer-only special casing for a small subset of public codes;
- recognized public codes falling through to the generic
  `executionUpdate / warning` card;
- ad hoc reconciliation-failure formatting outside the shared presentation
  catalog; and
- one overloaded message for backend `result_unavailable` and hydration-only
  `terminal_result_unavailable`.

The fixed generic `run_failed` fallback was intentionally retained. Removing it
would expose unclassified failures or invite raw exception rendering.

## Module Disposition Ledger

| Module or group | Role | Audit result |
| --- | --- | --- |
| `app/queue.py`, Run repositories, worker admission | Queue and Run authority | Searched and retained. No browser compatibility event ownership. |
| Chat/Run submit and status routes | Initial queued response and authoritative status | Searched and retained. `queue_position` remains valid. |
| `app/streaming/redis.py`, Streaming application/domain modules | SSE v3 open, replay, live delivery | Searched and retained. No `queue_update` publisher exists. |
| SSE v3 schema and generated Python/TypeScript types | Public wire contract | Searched and retained unchanged. |
| `useAgent.ts` | Submission toast and terminal hydration owner | Searched and retained; hydration-unavailable wording separated. |
| `publicRunStreamV3.ts` | Strict v3-to-Chat adapter | Queue repair owner: explicit internal `stream_open`. |
| `eventHandlers.ts` and internal event types | Ownership/cursor fences and reducer dispatch | Queue repair owner: accepted current `stream_open`; dead `queue_update` removed. |
| Context/file parsers and validators | Internal bounded failure producers | Searched and retained. Not public text authority. |
| Executor, sandbox, repository, reconciler | Private operational diagnostics | Searched and retained behind public projection. |
| `app/run_projection.py` | Public terminal taxonomy and fallback | Searched and retained unchanged as classification authority. |
| Run/LambChat projection routes | Live/history public projection | Searched and retained; no second classification map. |
| Public payload and memory redaction | Non-disclosure boundary | Searched and retained unchanged. |
| `publicTerminalPresentation.ts` | Complete frontend fixed-code catalog | Extracted as the sole presentation owner; retains the ADR 0011 reconciliation code as explicitly dormant. |
| `eventProcessor.ts` | Live/replay/hydration reducer projection | Embedded partial map retired; complete catalog reused. |
| `historyLoader.ts` | Historical reconstruction | Searched and retained; uses the unified processor. |
| `MessagePartRenderer.tsx` | Visible status card | Partial hard-coded mapping retired; catalog reused. |
| `zh.json` | Chinese presentation text | Completed for every catalog entry; no extra locale invented. |
| Admin tools and metrics | Authorized operational visibility | Searched and retained; not ordinary-user Chat projection. |
| Superseded SSE ADRs and release evidence | Historical audit record | Searched and retained as history only, never runtime fallback. |

## Verification Ownership

The executable evidence is split by boundary:

- adapter tests prove strict `stream_open` mapping and malformed-frame rejection;
- handler tests prove current Run/session/generation/cursor fencing, toast cleanup,
  cursor-only commit, and no message mutation;
- routed-session coverage exercises queued submission through a real v3 stream
  lifecycle;
- backend/frontend parity tests compare every public backend code and alias with
  the frontend catalog and locale;
- event-processor matrix tests cover all public detail codes, kind mismatch,
  unknown code, malicious backend message rejection, and partial-content
  preservation;
- history tests prove PDF-password reconstruction without backend-message
  disclosure; and
- renderer tests prove every catalog entry produces its fixed visible title and
  detail.

Exact searches after implementation must find no active `queue_update` and no
`terminal_reconciliation_failed` producer outside its future backend owner. The
latter may appear only in ADR 0011, the dormant frontend catalog, its locale and
tests, and this disposition record until that owner is delivered. Generic
status text may remain for non-terminal operational events and unknown/private
failures; that is a deliberate safety disposition, not an incomplete cleanup.

## Evidence Ceiling

Source audit, static checks, and local tests do not prove deployed browser
behavior. Runtime acceptance requires an immutable packaged image and fresh
s72 observations of:

1. a queued Run clearing `chat-queue` on its accepted current-Run
   `stream_open`, before terminal; and
2. an encrypted-PDF failure rendering the fixed password action while an
   unknown/private failure remains generic and leaks no raw diagnostic data.

Runtime evidence must not record user document content, raw exceptions,
credentials, tenant identifiers, or principal identifiers.
