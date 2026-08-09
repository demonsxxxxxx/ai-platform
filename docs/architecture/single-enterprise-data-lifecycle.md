# Single-enterprise identity and data lifecycle

This document is the durable contract for the current AI Platform deployment.
It describes source behavior; it is not evidence that a particular host has
been upgraded.

## Product and identity boundary

AI Platform is a single-enterprise, multi-user, concurrent system. The existing
`tenant_id = "default"` value is an internal deployment-scope key used to keep
queries, queues, leases, and storage paths scoped consistently. It is not a
customer-selectable tenant and does not introduce tenant management.

Department, role, and permission facts come from the existing company login and
user-info authority. Ordinary clients cannot choose them. A trusted gateway may
inject principal headers only when it presents the configured shared secret.
Production configuration fails during startup when the secret is absent or the
frontend POC header path is enabled. Company principals are revalidated against
the current authority before worker dispatch.

Successful login and run admission retain only the non-sensitive facts needed
to explain an authorization decision: `department_id`, authorization policy
version, authority source, and authority check time. Raw DingTalk or user-info
responses, credentials, gateway secrets, and full upstream payloads must not be
stored. A department directory, if added for labels or configuration, may only
be a short-TTL cache; an upstream outage must deny or preserve the last stricter
ACL and must never create a second department authority.

## Data ownership

| Data class | Authority | Contract |
| --- | --- | --- |
| Users, sessions, runs, messages, artifact metadata, ACL and audit facts | PostgreSQL | Durable business facts and authorization evidence. |
| Queue entries, leases and bounded SSE transport | Redis | Ephemeral coordination only; it is not the terminal record. |
| Uploaded files, generated artifacts and Skill packages | MinIO/S3 | Object bytes live here; PostgreSQL stores keys, digests, sizes, schemas and bounded summaries. |
| Executor workspace | Sandbox filesystem | Attempt-scoped temporary copy; never a durable authority. |
| `assistant_delta` | Redis/SSE projection | It is deliberately not re-persisted into PostgreSQL; durable messages and terminal events remain the source of truth. |

Full raw prompts, Claude transcripts, file bytes, and sandbox directories are
not valid PostgreSQL payloads.

## Schema lifecycle and readiness

The supported upgrade command is:

```text
python -m app.schema_migrations apply
```

The runner takes one PostgreSQL transaction-scoped advisory lock, creates the
`schema_migrations` ledger when bootstrapping, applies the additive idempotent
schema, and records the target version plus SHA-256 checksum in the same
transaction. Concurrent API, worker, or operator attempts therefore serialize;
a failed transaction remains unapplied and can be retried. Every committed
schema change must advance `TARGET_SCHEMA_VERSION`; reusing a version with
different SQL fails closed on checksum mismatch.

Compose runs this command as a one-shot `migrate` service before API and worker
startup. The legacy authenticated `POST /admin/apply-schema` endpoint delegates
to the same runner for compatibility; it is not the normal release path.
`python -m app.schema_migrations status`, API readiness, and worker startup all
verify the target ledger entry, checksum, and critical relations. Connectivity
alone is insufficient.

Upgrade procedure:

1. Keep the previous application image available and apply the new schema with
   the migration command. Repeated or concurrent invocation is safe.
2. Require a successful `status` result before starting API and workers.
3. Deploy the matching API, worker, and frontend commit, then verify `/ready`.

Migrations in this phase are additive. Rollback means restoring the previous
application image only after confirming it tolerates the added columns,
indexes, ledger, and outbox tables. Do not drop or rename them during rollback.
There is no automatic down migration. A checksum mismatch or missing critical
contract is a stop condition requiring operator investigation, not a reason to
bypass readiness.

## Bounded reads and compatibility

Session message history is ordered by `(created_at, id)` and fetched by an
opaque, session-bound cursor. The existing response shape remains valid and now
adds nullable `next_cursor`; requests default to 100 and are capped at 200.
Fork/context reads and the public Agent Market query also have hard backend
limits, so the UI and executor do not load an entire conversation or catalog in
one query. Composite indexes support the tenant/session/run/time access paths.

Clients should continue from `next_cursor` until it is absent. A cursor from a
different session is rejected. Existing clients that do not send a cursor keep
working but receive only the bounded first page.

## Retention and physical deletion

Cleanup runs in small worker batches and is retryable:

- Expired artifacts are selected with `FOR UPDATE SKIP LOCKED` only when no
  active run/session, context snapshot, or audit target still references them.
  PostgreSQL first marks the row `delete_pending` and creates one unique object
  deletion outbox record. MinIO deletion is then attempted outside the database
  transaction. Success writes the receipt and tombstone; failure records a safe
  error code and is reclaimed after its lease. Artifact reads exclude expired
  or non-active rows throughout the process.
- Soft-deleted memory is physically purged only after the configured grace
  period and only when no active session, context snapshot, or audit target
  still references it. Selection is bounded and uses `SKIP LOCKED`.
- `run_events` (including their `run_event_batches` binding), context snapshots,
  audit rows, messages, and files expose explicit retention-day settings. Their
  default is `0`, which means retain and do not physically delete. Until product
  retention and reference rules are approved for a class, the runtime reports
  it in `disabled_fail_safe` instead of guessing a deletion policy.

`GET /admin/retention/status` exposes the policy projection plus pending,
processing, failed, and deletion-ready backlog counts. Disabling cleanup never
turns into implicit deletion. The worker's artifact and memory maintenance may
be paused independently through the documented environment settings.

## PostgreSQL payload bounds

Limits are measured as UTF-8 bytes; JSON uses its compact serialized form.
Oversized values fail before their write with a stable `*_too_large` error.

| Value | Maximum |
| --- | ---: |
| Run input or result | 256 KiB |
| Run-event payload | 64 KiB |
| Run-event message | 16 KiB |
| Context snapshot payload | 256 KiB |
| Artifact manifest | 64 KiB |
| Audit payload | 32 KiB |
| Message content | 256 KiB |
| Message metadata | 64 KiB |

Large files and generated outputs continue to use MinIO/S3. Raising a limit
requires a reviewed schema/query impact assessment; it is not a client option.

## Explicit non-goals

This contract does not change single-server port exposure, Docker network
hardening, PostgreSQL/MinIO backup or PITR, Compose CPU/memory limits, disk
isolation, PgBouncer, sharding, replicas, Kafka/ClickHouse, or OpenSandbox
single-host resource isolation.
