# P1 Memory Retention Worker Cleanup Design

## Goal

Close the scheduled-cleanup part of the P1 Memory / Context Management hardening gate by letting the existing worker perform bounded expired-memory cleanup on a configurable interval.

## Scope

- Add worker-side retention cleanup for expired `memory_records`.
- Reuse `repositories.cleanup_expired_memory_records`.
- Use the configured default tenant and workspace only for this slice.
- Add settings for enablement, interval, and batch limit.
- Write an operational audit row only when records are deleted.
- Keep cross-session long-term memory fail-closed.

Out of scope:

- Configurable redaction policy.
- A new scheduler service.
- Multi-workspace scanning.
- New public or admin HTTP contracts.
- Docker validation on the local Windows workstation.

## Architecture

The worker already performs maintenance before queue leasing: sandbox lease cleanup, queue reclaim, then run lease. This slice inserts memory retention cleanup between sandbox cleanup and queue reclaim. The ordering keeps platform-owned cleanup before new run processing and preserves the existing fail-closed behavior where maintenance failure stops queue leasing.

The cleanup tick is process-local and bounded. A small module-level timestamp records the next allowed cleanup time. Each `run_once` checks settings and the timestamp. When due, it opens one transaction, calls the repository cleanup helper with `default_tenant_id`, `default_workspace_id`, and the configured limit, and appends a redacted operational audit row if rows were deleted.

## Data And Audit

The repository helper returns only operational metadata and never returns memory content. The worker audit payload includes:

- `workspace_id`
- `deleted_count`
- `memory_record_ids`
- `target_user_ids`
- `reason = retention_expired`
- `source = worker`

The audit row uses:

- `action = worker.memory.retention.cleanup`
- `target_type = memory_retention`
- `target_id = <workspace_id>`
- `user_id = null`

No content, metadata payload, executor private payload, secret values, or runtime paths are added to the audit payload.

## Settings

Add the following settings to `app.settings.Settings`:

- `memory_retention_worker_cleanup_enabled: bool = True`
- `memory_retention_worker_cleanup_interval_seconds: float = 300.0`
- `memory_retention_worker_cleanup_limit: int = 200`

The worker skips cleanup when disabled, when the interval is not due, or when the configured limit is not positive. A non-positive limit is treated as disabled for worker maintenance so an invalid environment setting does not trigger repeated repository conflicts on every poll.

## Testing

Focused tests in `tests/test_worker_main.py` cover:

- Due cleanup runs before queue reclaim, uses default tenant/workspace, writes audit only for deleted rows, and does not expose content.
- Repeated `run_once` before the interval elapses skips the repository scan.
- Disabled cleanup skips repository calls.

Existing route and repository tests continue to cover the manual admin cleanup contract and the SQL soft-delete behavior.

## Deployment

Local verification uses repository-native commands with workspace-local pytest temp paths. Docker build, compose recreation, container labels, and the smoke test run only on 211.

The 211 smoke seeds an expired memory record in the default tenant/workspace, runs the worker cleanup path once, verifies soft-delete and audit evidence, checks API health and API/worker labels, and removes all smoke data.
