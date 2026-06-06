# G5 Tenant-Aware Queue Lease Design

## Goal

Close the next G5 scheduler gate by making worker leasing tenant/user aware,
bounded, observable, and compatible with the current Redis queue.

## Current Context

P1 Admin Runtime, Memory / Context Management, and Tool Permission / Agent
Frontend V1 are already verified enough to proceed. The foundation roadmap
still blocks broader P2 Multi-Agent Runtime on tenant-aware scheduling, quota,
backpressure, bounded queue metadata, worker maintenance, and queue
observability.

The current worker queue uses one global Redis list for queued runs and one
global processing list. `lease_run()` respects only global worker capacity.
`get_queue_insight()` reports global depth and same-tenant queued/processing
counts, but it does not report tenant/user quota saturation or bounded scan
settings.

## Approaches Considered

1. Admission-only enqueue quota.
   This is low risk, but it does not prevent one tenant from occupying all
   processing slots after work is already queued. It is insufficient for fair
   scheduling.

2. Single global queue with bounded tenant/user-aware leasing.
   This keeps the current queue shape, avoids a migration, preserves existing
   enqueue and retry semantics, and lets workers bypass saturated tenants/users
   within a configured scan window. This is the selected approach.

3. Per-tenant Redis queues with round-robin scheduling.
   This is stronger long term, but it changes queue topology, queue position,
   cleanup, retry, and smoke semantics. It should wait until the first G5
   gate is proven.

## Contract

When tenant or user processing limits are configured, `lease_run()` scans at
most `queue_lease_scan_limit` queued entries and leases the first valid payload
whose tenant and user are below their processing limits. Saturated candidates
remain queued. Valid later candidates can be leased to avoid noisy-neighbor
starvation.

When tenant and user limits are disabled, `lease_run()` keeps the existing
blocking `brpoplpush` fast path so current worker behavior stays compatible.

The scan is bounded and fail-closed:

- Global `max_processing_runs` still gates all leasing.
- `queue_lease_scan_limit <= 0` returns idle when quota mode is requested.
- Invalid queued payloads encountered during bounded scan are dead-lettered and
  removed from the queue instead of blocking later valid candidates.
- If all scanned valid candidates are quota blocked, the worker returns idle
  without moving any blocked item to processing.
- Processing metadata records `tenant_id`, `user_id`, `run_id`, `worker_id`,
  attempt count, lease timestamps, and a public-safe `quota_snapshot`.
- Queue projections expose quota limits and bounded scan limits. Public/user
  projections expose only the current user's throttling state; admin-only
  projections may request same-tenant user breakdown. They never expose raw
  queue payloads, executor private payload, runtime paths, storage keys, or
  secret-like data.

## Settings

Add these settings:

- `queue_tenant_processing_limit: int = 0`
- `queue_user_processing_limit: int = 0`
- `queue_lease_scan_limit: int = 50`

`0` means the limit is disabled. The default deployment remains backward
compatible while operators can enable the gate on 211 for smoke validation.

## Worker Flow

`worker_main.run_once()` continues to run sandbox cleanup, memory cleanup,
multi-agent dispatcher maintenance, queue reclaim, and then queue lease. The
lease call passes global worker capacity, tenant limit, user limit, and scan
limit from settings.

## Admin Runtime / Projection Flow

`get_queue_insight(tenant_id)` adds:

- `capacity.queue_tenant_processing_limit`
- `capacity.queue_user_processing_limit`
- `capacity.queue_lease_scan_limit`
- `throttling.tenant_processing`
- `throttling.tenant_processing_saturated`
- `throttling.current_user` for public/current-user views
- `throttling.users` only when an admin route explicitly requests same-tenant
  user breakdown
- `throttling.user_processing_limit`

This makes Admin Runtime show whether queue pressure is global capacity,
tenant throttle, user throttle, or ordinary backlog.

## Boundaries

This slice does not migrate Redis topology, add per-tenant queues, open new
sandbox/tool privileges, create a frontend entry, change executor private
payloads, or advance P2 multi-agent exposure. Local Docker remains unsupported;
Docker/compose/runtime smoke stays on 211.

## Verification

Focused tests must prove tenant saturation bypass, user saturation bypass,
bounded scan idle behavior, invalid payload dead-lettering during bounded scan,
worker settings propagation, queue insight throttling projection, and unchanged
legacy lease fast path when limits are disabled.

Stage verification must include compile, focused queue/worker/admin tests, full
local pytest, inherited-configuration multi-agent review when available, and
211 smoke with a temporary queue prefix. The 211 smoke must enable quota
settings only for the smoke process or container runtime, avoid touching live
queue keys, verify no secret/private payload leakage, and clean temporary Redis
keys.
