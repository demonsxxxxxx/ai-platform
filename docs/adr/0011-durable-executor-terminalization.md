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
6. Terminal reconciliation has a bounded failure result. Once the configured retry budget is exhausted, the run reaches `failed` with code `terminal_reconciliation_failed`; existing safe partial text and authorized artifacts remain available.
7. The frontend maps this structured code to a specific explanation, remediation and correlation ID. It never renders a raw traceback or executor payload.

## Consequences

- New snapshot fields require explicit metadata additions rather than changing `RunPayload`.
- Stale cleanup may leave a run active until the terminal reconciler resolves it, rather than treating a completed receipt as ownerless.
- Operations can alert on terminal receipt age, retry count and permanent reconciliation failure from durable fields.
- Existing v1 snapshots must remain supported until all pre-existing leases expire or are migrated.

## Rejected Alternatives

- Increasing stale timeouts only delays incorrect cancellation.
- Marking a run successful directly in the callback skips artifact collection and adapter validation.
- Retrying forever hides permanent compatibility failures and consumes capacity.
- Rendering raw failures in the browser exposes private paths, tokens and executor diagnostics.
