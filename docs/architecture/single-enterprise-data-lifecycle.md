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
the current authority before worker dispatch. Browser and bearer principal
snapshots preserve the policy version, authority source, and check timestamp;
policy mismatch or authority facts older than the configured short freshness
window (15 minutes by default) fail closed and require login refresh. This is
intentionally shorter than the browser context's maximum lifetime.

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
core schema, and records the target version plus SHA-256 checksum in the same
transaction. New hot-table indexes are a separate resumable phase recorded in
`schema_index_migrations`: each is built with `CREATE INDEX CONCURRENTLY`
outside the core transaction, catalog validity is checked before it is marked
ready, and an interrupted or invalid build is safely retried. Concurrent API,
worker, or operator attempts serialize without holding a transaction open
across the concurrent index build. Every committed schema change must advance
`TARGET_SCHEMA_VERSION`; reusing a version with different SQL fails closed on
checksum mismatch.

Compose runs this command as a one-shot `migrate` service before API and worker
startup. The legacy authenticated `POST /admin/apply-schema` endpoint delegates
to the same runner for compatibility; it is not the normal release path.
`python -m app.schema_migrations status`, API readiness, and worker startup all
verify the target ledger and index-ledger entries, checksum, critical column
types/nullability, named constraints, and valid/ready indexes. Connectivity or
relation existence alone is insufficient.

Upgrade procedure:

1. Keep the previous application image available and apply the new schema with
   the migration command. Repeated or concurrent invocation is safe.
2. Require a successful `status` result before starting API and workers.
3. Deploy the matching API, worker, and frontend commit, then verify `/ready`.

When a schema version adds a new deletion-outbox target, every object-deletion
worker must understand that target before an API can enqueue it. In particular,
an artifact-only worker must never claim a file-target row: it could delete the
object bytes without writing the matching file lifecycle receipt. The standard
Compose rollout avoids this mixed state by starting API and worker from the
same post-migration image. A rolling deployment must stop or drain old workers,
deploy target-aware workers, and only then enable the producer route.

Migrations in this phase are additive. Rollback means restoring the previous
application image only after confirming it tolerates the added columns,
indexes, ledger, and outbox tables. Do not drop or rename them during rollback.
There is no automatic down migration. A checksum mismatch or missing critical
contract is a stop condition requiring operator investigation, not a reason to
bypass readiness.

Before rolling back to an artifact-only worker, stop the file-delete producer
and drain or reconcile every non-terminal file-target outbox row with a
target-aware worker. If that cannot be proved, roll forward. The additive file
lifecycle and typed-outbox columns may remain installed; dropping them is not a
safe application rollback.

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
  transaction. Success writes the receipt and tombstone. Failure records only a
  safe error class and retries with capped exponential backoff. After the
  configured maximum attempts it moves to `dead_letter`, is excluded from
  automatic claims, raises an admin status alert, and requires an explicit
  tenant-scoped operator requeue. A stale processing lease represents an
  unknown outcome and is retried idempotently before a receipt is committed.
  Artifact reads exclude expired or non-active rows throughout the process.
- An authenticated owner may explicitly request deletion of a persisted file
  that is still unbound. This is separate from age-based file retention. The
  request locks the exact `(tenant, workspace, user, file)` row using the same
  row authority as Run binding and fails closed for a Session/Run binding,
  `runs.input_json.file_ids`, message `metadata_json.file_ids`, context snapshot
  `included_file_ids`, artifact `source_file_id`, or a shared live artifact
  storage key. An eligible row becomes `delete_pending` and gets one typed file
  target in the shared object-deletion outbox; the HTTP transaction never
  deletes object bytes. Duplicate requests return the existing lifecycle state
  only when target, storage key, and outbox state still agree.
  File-target audit rows record the lifecycle decision but do not keep object
  bytes live; treating the deletion audit itself as a reference would make
  every accepted request permanently undeletable. Run events remain an audit
  projection rather than a file-binding authority.
- Object-deletion claims increment a monotonic `lease_generation` that is never
  reset by operator requeue. Completion and failure require that exact
  generation, so a worker whose lease expired cannot receipt or fail a newer
  claim. Completion updates the exact artifact or file target and its outbox
  receipt in one transaction; a missing target, wrong storage key, or unexpected
  lifecycle state remains retry/dead-letter/reconcile work instead of a false
  success. Existing artifact rows are not rewritten when the typed target
  column is introduced.
- Soft-deleted memory is physically purged only after the configured grace
  period and only when no active session, context snapshot, or audit target
  still references it. Selection is bounded and uses `SKIP LOCKED`.
- `run_events` (including their `run_event_batches` binding), context snapshots,
  audit rows, messages, and files expose explicit retention-day settings. Their
  default is `0`, which means retain and do not physically delete. Until product
  retention and reference rules are approved and a reference-safe cleaner is
  implemented, `0` is reported as `disabled_fail_safe`; a non-zero value is
  reported as `unsupported_not_implemented` and maintenance performs no delete.
  Production settings reject those non-zero values during startup.

Owner-requested file deletion does not make non-zero `file_retention_days`
supported. It also begins only after a `files` row exists. Object bytes written
before a failed metadata insert require a separate orphan-reconciliation
contract and are not covered by the owner delete endpoint.

`GET /admin/retention/status` exposes the policy projection, object-outbox state
counts (including dead-letter/reconcile-required alerts), and age-eligible row
counts for unsupported classes. A dead-letter item can only be requeued through
the admin reconcile endpoint after operator review. Unsupported-class counts
are observability only, not deletion eligibility or a claim that cleanup exists.
Disabling cleanup never turns into implicit deletion. The worker's artifact and
memory maintenance may be paused independently through the documented
environment settings.

## PostgreSQL payload bounds

Limits are measured as UTF-8 bytes; JSON uses its compact serialized form.
Copied-run execution snapshots and accumulated run-step patches are validated
against the final merged value while the row is locked, so concurrent updates
cannot bypass the bound. Oversized values fail before their write with a stable
`*_too_large` error and leave the prior value unchanged.

| Value | Maximum |
| --- | ---: |
| Run input or result | 256 KiB |
| Run-event payload | 64 KiB |
| Accumulated run-step payload | 64 KiB |
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
