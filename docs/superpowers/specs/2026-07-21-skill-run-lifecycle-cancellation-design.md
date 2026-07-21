# Skill Run Lifecycle Cancellation Design

## Scope and compatibility

This change repairs execution ownership at the existing executor-adapter seam. It does not add a second execution path, change persistence or queue schemas, change public response shapes, or make read/status/stream routes create or enqueue runs. Existing tenant, workspace, user, session, run, agent, Skill, release-lock, permission, event, parent-reconciliation, queue-lease, and terminal CAS rules remain authoritative.

## Deep module and interface

`app.executors.base` owns one per-run execution module. Its small interface is:

- wait for the adapter result;
- request stop with a reason and a finite timeout;
- return an observable stop result that distinguishes confirmed quiescence from timeout or stop failure.

The default adapter path owns the adapter task. `ClaudeAgentWorkerAdapter` uses the same module and threads its internal stop registrar into `SandboxRuntime`; when a real sandbox lease exists, the runtime registers the provider/container stop plus lease release in that same ownership chain. Local Claude SDK cancellation and remote sandbox cancellation therefore converge on one owner. No caller may infer quiescence merely from cancelling an asyncio task.

## Invariants and failure modes

1. The worker may persist `cancelled`, release its worker placeholder lease, reconcile a parent, and allow queue ACK only after the execution owner confirms quiescence.
2. A cooperative local adapter is quiescent only after its task has stopped. A sandbox run is quiescent only after both the task and registered provider stop are confirmed; a failed or timed-out provider stop is not quiescence.
3. Stop attempts are bounded. A timeout or stop failure is persisted as a non-terminal, observable run event, while the worker retains the queue lease and waits/retries through the same owner. It must not detach a possible writer or report cancellation as complete.
4. Runtime lease recording failure still stops the newly created container before surfacing the failure. Ephemeral success/failure cleanup still stops the container before releasing its lease. Cleanup exceptions remain failures and cannot be converted to `cancelled` unless a later owner stop confirms quiescence.
5. Success/failure/cancel terminal writes remain repository CAS operations. A success/cancel race is classified from durable run state; artifacts and assistant messages must not survive a lost success CAS transaction.
6. While an adapter is silent, the worker periodically persists a truthful progress heartbeat through the existing run-event projection. It never invents assistant text. Existing assistant deltas and Skill/tool events continue to be persisted immediately and replayed by SSE.
7. GET status/history and SSE reconnect read the same principal-scoped persisted run ID. They never create, copy, retry, resume, or enqueue a run.

## Verification

Focused tests cover cooperative and non-cooperative adapters, bounded stop success/timeout/failure, sandbox lease-not-established and cleanup exceptions, no premature terminal/lease release/queue ACK, success/cancel races, silent progress heartbeats, and terminal SSE replay. Required gates are full `tests/test_worker.py`, focused adapter/runtime/LambChat/worker-main tests, `compileall`, and `git diff --check`.
