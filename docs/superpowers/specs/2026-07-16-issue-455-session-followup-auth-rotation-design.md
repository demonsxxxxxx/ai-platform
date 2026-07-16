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
   silently create a replacement session. A missing/foreign session fails closed.
3. A request that fails before a confirmed `session_id` and `run_id` restores
   the prior message list, preserves the draft for retry, and emits only a
   stable actionable transport error. It never manufactures an assistant turn.
   Confirmed runs retain the existing SSE/reconciliation lifecycle.

## Ordering and race constraints

- The auth-scope reset must increment the same generation/token fences used by
  `clearMessages`; a late history, submit, title, or stream completion must not
  repopulate the new principal's view.
- The backend lookup remains exact tenant/user ownership. There is no fallback
  search, session reassignment, or cross-scope agent reuse.
- Cookie authority and the #453 auth-context generation/CAS protocol are not
  changed. No HTTP mutation is retried automatically.

## Compatibility and verification

Fresh chats keep existing intent routing. Owned existing sessions continue with
their persisted agent, preserving existing session/run foreign-key invariants.
Focused frontend coverage will exercise auth-scope rotation, fresh submit,
owned follow-up, and a stale completion. Focused backend coverage will exercise
owned continuation and foreign-session rejection. Validation will use the
changed-scope frontend tests, chat-route tests, TypeScript/lint/build as needed,
`python -m compileall -q app tools scripts`, and `git diff --check`; no full
pytest suite or deployment is in scope.
