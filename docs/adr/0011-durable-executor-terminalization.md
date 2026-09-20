# ADR 0011: Durable Executor Terminalization

## Status

Accepted

## Context

An executor can persist a terminal callback while its parent run remains active. Terminal conversion may need artifact collection and adapter result conversion, so the callback cannot directly mutate the run. The prior design mixed execution payload fields with reconciliation metadata, retried all conversion failures without a durable failure boundary, and allowed stale-run cleanup to cancel a run after a terminal receipt existed.

## Decision

1. PostgreSQL is authoritative for executor terminal receipts, reconciliation snapshots, attempts and run terminal state. Redis only wakes live consumers.
2. Reconciliation snapshots use `ai-platform.executor-reconciliation-snapshot.v2` with separate `execution_payload` and `metadata`. Legacy snapshots remain readable.
3. Reconciliation metadata never flows into `RunPayload` construction. Snapshot readers validate the version and explicitly select the execution payload.
4. A pending executor terminal receipt is an ownership proof. Stale-run cleanup must not terminalize a run with such a receipt.
5. Reconciliation errors are structured, durable and safe to project. Unexpected exceptions include a full server-side traceback in logs, never in public events.
6. Terminal reconciliation distinguishes explicit permanent validation/authority
   errors from transient dependency failures. Permanent errors converge the Run
   to `failed` with code `terminal_reconciliation_failed`; transient errors stay
   durably pending and are retried without an attempt-count cutoff.
7. The frontend maps this structured code to a specific explanation, remediation and correlation ID. It never renders a raw traceback or executor payload.
8. Retry claims are delayed by PostgreSQL backoff. A terminal receipt persisted by suspect-task probing immediately returns the scheduler to terminal draining, while a still-running probe reports no progress and cannot create a hot loop.
9. Every Worker artifact key includes the durable execution attempt and a digest
   of the exact bytes. Before each ObjectStorage write, the adapter creates or
   rearms the matching object-deletion receipt. Same-content retries therefore
   reuse one owner and cannot amplify durable cleanup state; changed bytes use
   another key and cannot be overwritten by retained I/O. The Run terminal
   transaction atomically promotes each receipt into a public artifact, while
   unpromoted receipts keep periodically deleting their exact key so retained
   I/O, rollback and process loss cannot orphan an object.

## Consequences

- New snapshot fields require explicit metadata additions rather than changing `RunPayload`.
- Stale cleanup may leave a run active until the terminal reconciler resolves it, rather than treating a completed receipt as ownerless.
- Operations can alert on terminal receipt age and retry count. Explicit
  permanent reconciliation failures remain visible from durable fields.
- Existing v1 snapshots must remain supported until all pre-existing leases expire or are migrated.

## Rejected Alternatives

- Increasing stale timeouts only delays incorrect cancellation.
- Marking a run successful directly in the callback skips artifact collection and adapter validation.
- Retrying forever hides permanent compatibility failures and consumes capacity.
- Rendering raw failures in the browser exposes private paths, tokens and executor diagnostics.
