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
   and enables a manual retry. A known-session history/status refresh alone
   cannot prove an unknown POST has stopped; both existing and fresh
   submissions remain blocked until the durable resolver or explicit admission
   retry returns a server-defined outcome.

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

## Generation 4: durable submission resolution

The earlier unknown-outcome fence is insufficient: a history GET can complete
before the preceding chat POST commits, and an unknown fresh submission has no
trusted session id to reload. The authoritative seam is therefore a keyed chat
submission resolution flow, not a frontend error-code guess.

1. A keyed request creates one tenant/user-scoped ledger record with an
   immutable effective workspace, a server-computed canonical request hash,
   and either a stable pre-persistence rejection or the session/run created in
   the same database transaction. The unique key is
   `(tenant_id, user_id, submission_id)`; workspace is deliberately not part
   of that uniqueness constraint, so a changed workspace cannot create a
   second mutation under the same key.
2. The existing chat route remains the sole session/run creation authority.
   Its keyed branch resolves an existing ledger record before intent,
   capability, file, queue, run, or context work. Same key plus hash returns
   the recorded outcome; a changed hash returns a non-leaking conflict.
3. Accepted records begin as `accepted_pending_enqueue`. Queue delivery is
   rebuilt from the already-authoritative run execution snapshot and is
   idempotent for the deterministic tenant/run/message identity across queued,
   processing, and retry Redis state. A delivery failure remains truthful and
   retry admission cannot create another chat mutation.
4. A principal-scoped resolver exposes only the caller's exact submission;
   an explicit retry-admission operation takes no user payload. The browser
   persists only the opaque key and a structural owner tuple, resolves through
   the server after refresh, and never treats ordinary history absence as
   proof of rejection or performs automatic POST replay.
5. A server-controlled `rejected_before_persist` disposition replaces the
   frontend allowlist. Before a resolver/history publication can unlock the
   composer, it updates `messagesRef.current` and all generation-owned state
   synchronously. The layout-phase auth reset remains the authority for a
   principal replacement.

The change is additive. Callers without a key retain the legacy route and no
dedupe guarantee. Rollout is schema/backend first: a frontend only enables
keyed recovery after an exact submission-id echo; a backend that silently
ignores the optional field remains fail-closed. Ledger rows are intentionally
not deleted in this slice, so a cleanup cannot resurrect an old key as a new
mutation; bounded retention requires a later tombstone/expiry design.

## Generation 5: durable browser and principal ordering repair

1. `chat_submissions.user_id` has an immediate foreign key. Every keyed claim
   and every persisted pre-persistence rejection therefore first provisions
   the trusted authenticated principal and verifies the resulting row in the
   same tenant, within the same transaction. A conflicting cross-tenant user
   id fails closed; no ledger row is written under an unverified principal.
2. Continuation lookup precedes a ledger claim. Once the exact owned session
   is resolved, its saved workspace is the value recorded immutably in the
   ledger, including deterministic rejection records. A client workspace never
   becomes a parallel ledger scope for an owned session.
3. Browser recovery writes one independently addressed record per
   `(tenant_id, user_id, submission_id)`, reads it back, and only then issues
   the chat POST. It retains all unresolved records and reads the prior
   aggregate record format only for migration compatibility. A quota/private
   storage failure prevents the network mutation rather than losing its
   recovery fence.
4. On each auth tuple replacement, the layout phase immediately installs the
   persisted fence and increments an auth-scope epoch before any resolver GET.
   Resolver and retry continuations capture that epoch, the session generation,
   and the submission id; an A1 completion after A-to-B-to-A2 cannot clear or
   publish into A2. A single resolver owner also avoids duplicate concurrent
   GETs while the same fence is live.
5. Resolver-confirmed `needs_confirmation` uses the same confirmation
   projector as the live response. It updates `messagesRef.current` before
   rendered state, clears the stale unavailable error, and only then unlocks
   the completed record. Unknown, absent, or malformed outcomes remain
   fenced.

The PostgreSQL schema node is deterministic but requires
`AI_PLATFORM_S0A_SCHEMA_TEST_DSN`; developer workstations without that DSN
skip it, while CI can exercise the first-principal immediate-FK path against a
real schema. No browser storage record contains chat text, attachments,
credentials, or cookies.

## Generation 6: confirmation recovery has no transcript authority

A resolver response can establish that a submission stopped at
`needs_confirmation`, but it cannot establish ownership of an optimistic
assistant placeholder after reload. Resolver and retry recovery therefore keep
the confirmation suggestions in dedicated non-message hook state and do not
append a synthetic assistant message. The live submit path may replace only
the optimistic placeholder it created for that same in-memory submission; when
that placeholder is absent, it also uses the non-message state.

Persisted recovery references additionally require a canonical lowercase UUID
and nonempty, at-most-128-character owner tuple elements. The reader removes
records that cannot have been produced by the protocol (including mismatched
per-record keys) while retaining and resolving other valid entries. This
quarantine is local-only; it never deletes a server submission or authorizes a
new POST.
