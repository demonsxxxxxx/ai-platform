# G5 Tenant-Aware Worker Maintenance Design

## Goal

Close the issue #16 worker-maintenance gap by making the worker-side expired
memory retention cleanup tenant-aware instead of default-tenant-only.

## Scope

- Replace the worker cleanup tick's `default_tenant_id/default_workspace_id`
  dependency with a bounded rotating scope cursor.
- Keep the existing enable, interval, and total row limit settings.
- Return only operational memory metadata from repository cleanup helpers.
- Write one redacted audit row per affected tenant/workspace group.
- Add an indexed schema path and persistent maintenance cursor for ordered
  expired-record cleanup.

Out of scope:

- New Memory UI or policy behavior.
- Cross-session long-term memory enablement.
- New scheduler service.
- Local Docker validation on this Windows workstation.

## Architecture

The worker still runs maintenance before queue leasing: sandbox cleanup, memory
retention cleanup, multi-agent dispatcher maintenance, queue reclaim, then queue
lease. The memory step becomes a bounded transaction that selects at most
`memory_retention_worker_cleanup_limit` tenant/workspace scopes from a
persistent `worker_maintenance_cursors` row and then soft-deletes at most the
configured total number of expired active `memory_records`.

The repository adds `cleanup_expired_memory_records_across_scopes(conn, limit)`.
It locks the `memory_retention_cleanup` cursor row, selects expired
tenant/workspace scopes after the previous cursor position, wraps to the
beginning when needed, updates the cursor to the last selected scope, and uses
`cross join lateral` with `for update skip locked` to delete a bounded
per-scope batch. The final candidate ordering prioritizes the first locked row
from each selected scope before filling the remaining budget, so a single
backlog-heavy tenant/workspace cannot consume the whole worker tick when other
selected scopes also have expired records.

A partial index on active non-deleted expiring records supports candidate scope
selection and per-scope cleanup without relying on a default-tenant scan.

## Audit And Redaction

The worker groups returned rows by `(tenant_id, workspace_id)` and appends
`worker.memory.retention.cleanup` audit entries under each tenant. The payload
contains only:

- `workspace_id`
- `deleted_count`
- `memory_record_ids`
- `target_user_ids`
- `reason = retention_expired`
- `source = worker`

The audit payload must not contain memory content, metadata JSON, executor
private payloads, storage keys, runtime paths, or secret-like values.

## Failure Behavior

Cleanup remains fail-closed for the worker maintenance path. Repository or audit
errors propagate out of `run_once`, so queue reclaim and new queue leasing do
not proceed after a failed maintenance pass.

Invalid or disabled settings continue to skip the cleanup tick without opening a
transaction. The process-local interval guard is updated only after a completed
scan.

## Testing

Focused tests cover:

- Worker cleanup deletes rows from multiple tenant/workspace scopes in one due
  pass and writes one sanitized audit row per scope.
- Worker cleanup no longer reads default tenant/workspace settings.
- Repository SQL uses the persistent scope cursor, per-scope lateral row cap,
  `for update skip locked`, and no content/metadata projection.
- Schema declares the partial expired-memory cleanup index and maintenance
  cursor table.
- Existing admin/manual memory cleanup stays scoped to the requesting tenant and
  workspace.

## 211 Verification

After local tests and review, deploy on 211 with image/source labels aligned to
the final main commit. Smoke should seed expired memory rows in at least two
tenant/workspace scopes, run the worker maintenance path once in the live worker
container, verify both rows soft-delete, verify per-scope sanitized audit rows,
check API/frontend health and clean logs, then remove all smoke data.
