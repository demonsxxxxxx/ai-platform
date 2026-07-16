# Issue #455: session continuation after auth-context rotation

## Scope

Repair ordinary-user chat submission after an administrator/ordinary-user
logout-login switch. The change is limited to the public chat submission path,
its session-selection state, and focused tests. Tool permission, runtime,
settings, OpenSandbox, and Issues #449/#450/#452/#454 are out of scope.

## Observed failure shape and hypothesis

`useAgent` renders optimistic user and assistant messages before admission. A
pre-admission API rejection is projected through the generic `chat.requestFailed`
copy, yielding the observed `错误：加载会话失败` card even though no assistant
turn or run exists. Independently, `POST /api/chat/stream` accepts a supplied
session id but derives the agent from the frontend request and validates the
session only during creation. A retained selection after auth-context rotation
can therefore reach a late scope/agent rejection instead of an explicit,
principal-scoped continuation decision.

## Design

1. Treat the authenticated `(tenant_id, user_id)` projection as a frontend
   session-selection generation. On a changed authenticated scope, synchronously
   invalidate pending history/submission/stream owners and clear the selected
   session before any later continuation can publish. A scope loss also clears;
   the initial anonymous-to-authenticated hydration does not retain an older
   selection.
2. On the backend, when `session_id` is present, load it only through the
   existing tenant-and-user authorized repository lookup before capability
   selection or persistence. Continue with the stored session agent; a client
   agent query cannot switch a loaded session, cross a user/tenant boundary, or
   silently create a replacement session. The stored session workspace becomes
   the effective workspace for capability-adjacent routing, file authorization,
   queue payload, run creation, and context persistence. `workspace_id=default`
   remains compatible with the current omitted/default wire value; a supplied
   non-default workspace that differs from the saved workspace fails before
   routing. A missing/foreign session fails closed.
3. A request that fails before a confirmed `session_id` and `run_id` restores
   the prior message list, preserves the draft for retry, and emits only a
   stable actionable transport error. It never manufactures an assistant turn.
   Confirmed runs retain the existing SSE/reconciliation lifecycle.
4. The identity boundary uses a layout-phase reset rather than a passive effect.
   It clears rendered and ref-held messages, session/run ownership, pending
   submission/history/stream/status fences, reconnect owners, and transport
   resources before the replacement principal can paint or act on the prior
   principal's state.
5. A chat admission response is not proof that the mutation was absent. The
   backend commits the session/run/user message before external queue admission,
   so network loss, 5xx, timeout, parse failure, or response loss retains the
   optimistic user turn and reports an unknown submission status without an
   assistant turn or automatic replay. Only a typed 4xx whose explicit code is
   known to be rejected before persistence restores the pre-submit message list
   and enables a manual retry. A known-session unknown result remains blocked
   until authoritative history/status refresh succeeds; an unknown fresh
   session requires a page-level refresh because no trusted session id exists.

## Ordering and race constraints

- The auth-scope reset compares the tenant/user tuple structurally (not through
  a delimiter-concatenated string) and must increment the same generation/token
  fences used by `clearMessages`; a late history, submit, title, or stream
  completion must not repopulate the new principal's view.
- The backend lookup remains exact tenant/user ownership. There is no fallback
  search, session reassignment, or cross-scope agent reuse.
- Workspace disagreement is rejected before intent, capability, file, queue,
  run, or context side effects. An owned non-default session therefore resumes
  in its saved workspace even when an older client omits the workspace.
- The layout reset changes the same monotonic history, submission, session, and
  stream generations used by existing #453 fencing, clears ref-visible state
  synchronously, aborts local stream/reconnect resources, and never sends a
  cross-principal cancellation request.
- Unknown mutation outcomes are not retried automatically. The UI may only
  clear that uncertainty through a successful authoritative refresh of the
  same known session, or through a new mounted auth owner.
- Cookie authority and the #453 auth-context generation/CAS protocol are not
  changed. No HTTP mutation is retried automatically.

## Compatibility and verification

Fresh chats keep existing intent routing. Owned existing sessions continue with
their persisted agent, preserving existing session/run foreign-key invariants.
Focused frontend coverage will exercise auth-scope rotation, fresh submit,
owned follow-up, an ambiguous colon-containing identity pair, and a stale
completion. Focused backend coverage will exercise owned continuation in a
non-default workspace, early workspace-mismatch rejection, and foreign-session
rejection. Focused frontend coverage also exercises a layout-phase identity
handoff with stale submit/reconnect/cancel work, typed pre-persistence 4xx
rejection, and network/5xx response loss after simulated server acceptance.
Validation will use the
changed-scope frontend tests, chat-route tests, TypeScript/lint/build as needed,
`python -m compileall -q app tools scripts`, and `git diff --check`; no full
pytest suite or deployment is in scope.
