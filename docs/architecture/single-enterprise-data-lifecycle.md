# Single-enterprise identity and data lifecycle

This document is the durable contract for the current AI Platform deployment.
It describes source behavior; it is not evidence that a particular host has
been upgraded.

## Product and identity boundary

AI Platform is a single-enterprise, multi-user, concurrent system. The existing
`tenant_id = "default"` value is an internal deployment-scope key used to keep
queries, queues, leases, and storage paths scoped consistently. It is not a
customer-selectable tenant and does not introduce tenant management.

Local `users` rows record authenticated business identities; they are not the
company employee inventory and must not gate pre-login employee grants. Identity
reads the company DingTalk employee directory for administrator model access
selection, projects only employee work ID and name, and validates new grants
against that same source. The historical model `allowed_user_ids` values are
company work IDs matching `AuthPrincipal.user_id`; DingTalk user IDs and department
IDs are never substituted. Employee selection shares the existing read-only
department directory's default origin and optional address override; it requires
no separate configuration. Directory reads use the fixed deployment scope and
fail closed without falling back to local users. The [model access contract](opensandbox-ephemeral-model-credentials.md#per-model-user-access)
owns eligibility, ambiguity handling, bounds and compatibility.

Role and permission facts come from the existing company login and user-info
authority; department grants come from the DingTalk membership source below.
Ordinary clients cannot choose them. A trusted gateway may inject principal
headers only with the configured shared secret; its department header grants
no department scope. Production startup rejects an absent secret or the frontend
POC header path.

Windows login first obtains the company-signed JWT from `GetADName`. AI Platform
accepts only that JWT, verifies its HS256 signature, issuer, audience, and
lifetime, and projects the signed identity and role claims into platform
authority. The original `depart` claim remains company metadata for JWT renewal.
Automatic reauthentication also supplies an optional `expected_principal`
constraint. A different verified user or tenant is rejected before session
commit; browser-supplied fields never grant identity or roles.
It does not trust browser-supplied identity fields or repeat the PermissionMS
user-info lookup already performed by `GetADName`. It separately resolves
DingTalk membership for the verified `workid`. The superseded AD request shape and login-time requery have no
compatibility owner and are removed atomically; password login and Worker
current-authority revalidation continue to use the existing user-info endpoint.

After a browser context expires, the shared HTTP transport can ask AuthProvider
to perform one fresh AD login before surfacing the expiry. Concurrent requests
share the attempt; safe replay requires the verified user and tenant to match
the previous principal. The browser never retains the password or uses an
expired credential as a new grant. [ADR 0007](../adr/0007-fixed-browser-authentication-day.md#automatic-company-reauthentication-after-context-expiry)
owns cancellation, replay and login-fallback behavior.

[ADR 0007](../adr/0007-fixed-browser-authentication-day.md) owns the browser
authentication lifetime: the signed platform token and server authentication
context retain one absolute, non-sliding 86,400-second day. A company-compatible
JWT has an independent 28,800-second lifetime capped by that context's remaining
authority. An expired JWT is rejected; a still-authenticated browser context may
renew it after a fresh company eligibility and permission check. The refresh
endpoint never accepts client-provided identity or an expired JWT as its grant.
Company username, department, and original role stay in the server-owned auth
snapshot, independently of the platform `roles` projection.

Refresh atomically commits the updated principal and encrypted MCP credential
under the context operation fence. Credential-only rotation preserves current
subject ownership; permission or department changes fence old owners. Logout,
identity replacement, and context expiry invalidate renewal. Legacy contexts
without the additional company fields recover those fields from the trusted
company user-info endpoint before renewal; they never infer company roles from
platform `admin/user`. Worker reauthorization does not imply instant
upstream-company revocation.

The per-connection SSE authorization lease is a separate mechanism described in
[Streaming execution control](redis-streams-sse-execution-control.md). Its short
frame-admission deadline is not the browser authentication lifetime and cannot
by itself invalidate an unrefreshed upstream company snapshot.

Successful login snapshots retain bounded identity, role, department scope,
authorization policy, source and check-time facts. Raw DingTalk or user-info
responses, credentials, gateway secrets and full upstream payloads must not be
stored. Historical Run `principal_department_id` remains audit provenance; replay
and dispatch check current authority, never infer grants from that scalar.

## DingTalk department authority

Identity resolves the authenticated work ID through the internal
`GET /api/DingTalk/employee/{workid}/department-ids` endpoint. The response contains
exactly `workid` and `department_ids`, and the work ID must match exactly. This
read-only endpoint follows the existing internal DingTalkAPI network boundary;
it uses no service key, Authorization header or custom authentication header.
Platform login validation, trusted work-ID binding and downstream ACL checks
remain required.

`DINGTALK_DEPARTMENT_AUTHORITY_BASE_URL` and the bounded timeout configure API and
Worker together. `DINGTALK_DEPARTMENT_AUTHORITY_TIMEOUT_SECONDS` (default 5 seconds,
maximum 30) applies to each membership, employee-directory and pure-department
HTTP request; the pure directory has no independent hard-coded timeout. A membership
lookup passes the same timeout to its ancestor-directory read. The same source
provides `/api/DingTalk/departs/pure`. Canonical
IDs are non-zero-leading ASCII decimal strings of at most ten digits. Identity
combines all direct memberships with their ancestors; missing parents, cycles,
overlong paths or unknown IDs reject the complete department scope. Names and
paths are display-only, including duplicate names.

Login, refresh and Worker dispatch use fresh membership. Authenticated requests
may reuse a 30-second, bounded in-process result. A failed lookup clears its old
result; a late concurrent lookup cannot restore an older scope. Empty configuration
or an unavailable source yields empty `direct_department_ids`, `department_ids`
and `department_authority_status="unavailable"`, preserving roles and identity.
Legacy sessions without these fields are read as unavailable. Refresh includes
all three fields in the existing authorization epoch comparison, so secondary
membership or availability changes fence old MCP execution owners as well.

Profile, Skill and MCP access uses the intersection of canonical ACL IDs and the
effective direct-plus-ancestor set. There is no name or scalar fallback. Existing
administrator, explicit-user, role and unrestricted semantics remain with their
owning authorizers. All administrator department writes validate against the
current directory; ordinary and system MCP forms use the shared selector.

Existing name grants require [administrator reselection](../operations/dingtalk-department-reselection.md)
through the owning forms before activation. Preserve immutable Profile revisions,
hashes and Run provenance; publish a new revision instead of rewriting history.
No schema migration or automatic name-to-ID conversion is part of this change.

## Data ownership

| Data class | Authority | Contract |
| --- | --- | --- |
| Users, sessions, runs, private Run diagnostics, messages, artifact metadata, ACL and audit facts | PostgreSQL | Durable business facts and authorization evidence. Private diagnostics use a separate tenant/Run-scoped bounded record and are not public answer data. |
| Queue entries, leases and bounded SSE transport | Redis | Ephemeral coordination only; it is not the terminal record. |
| Uploaded files, generated artifacts, Skill packages and private provider image payloads | MinIO/S3 | Object bytes live here; PostgreSQL stores keys, digests, sizes, schemas and bounded summaries. Provider session entry structure and original receipts remain in PostgreSQL; image payloads are restored losslessly under the [native continuity contract](claude-provider-session-continuity.md#private-image-persistence). |
| Executor workspace | Sandbox filesystem | Attempt-scoped temporary copy; never a durable authority. |
| Canonical v4 `message.delta` | PostgreSQL public Run-event ledger | Persists as one safe committed event with semantic identity/sequence, then publishes through the bounded Redis transport. |
| Legacy `assistant_delta` projection | Legacy ingress suppressed after V4 answer projection is accepted | It must not become a second durable answer producer. The ban on a PostgreSQL browser fallback does not forbid canonical v4 event persistence. |

Bounded user/assistant `messages` and authorized executor-private conversation
materialization are legitimate business data under the owning message/context
contracts. Arbitrary raw SDK transcripts, prompt/log dumps, file bytes and
sandbox directories are not valid Run-event or public-manifest payloads. Keep
message storage distinct from public projection and private operational logs.

### Object-storage bucket confirmation

An `ObjectStorage` instance owns one fixed client/configuration and caches only
successful bucket confirmation behind an instance-local thread lock. It never
shares this state across clients, credentials, endpoints or collections. A batch
of generated artifacts reuses its existing client and confirms the bucket once;
object writes, ordering and provisional cleanup reservations remain unchanged.
First-use list/create errors (including access denial and concurrent-create
conflicts) remain failures and are not cached. This does not add support for
credentials lacking the existing bucket-list permission.

An authoritative `NoSuchBucket` from an operation using that confirmation,
including a wrapped upload error, invalidates it for the next caller. The failed
operation still raises the original exception; no write is replayed here,
including on ambiguous timeouts. Normal durable retry/cleanup retains ownership.
No global client lifetime, upload parallelism or premature close is introduced.

### Object-storage download resource handoff

`ObjectStorage.download_to_tempfile` owns the response body and temporary path
until the download, result construction, and both required closes succeed. Only
then does it return the path, digest and size to the caller, which owns subsequent
local-file cleanup. A temporary-file creation failure still closes the acquired
response body. Before handoff, any failure attempts each acquired resource's
close independently and removes the temporary path when one was created.

The first operation or close failure remains the primary exception, including
size-limit failures and cancellation. Secondary close or unlink failures attempt
to add only the cleanup stage and exception type as best-effort notes. If the
exception rejects a note, that optional diagnostic failure cannot replace the
selected primary failure or interrupt later cleanup attempts. A close failure
after otherwise successful reads is still a failed download and triggers local
path cleanup; it cannot return a successful result. A failed optional note may
leave a secondary cleanup failure unreported; best-effort cleanup does not
establish that a resource was released when its close or unlink failed.

### Change Contract: persisted user profile metadata

- **Owner:** Identity owns authenticated user profile metadata; Company Navigation owns only its `company_navigation_favorite_ids` value.
- **Bounded paths:** this document, `app/schema.sql`, `app/schema_migrations.py`, `app/identity/api.py`, `app/identity/infrastructure/postgres.py`, the existing authenticated profile routes, Company Navigation frontend state, and their focused tests.
- **Reached invariants:** company login remains identity authority; the returned employee `workId` remains `AuthPrincipal.user_id` and `users.id`; every metadata read and write binds both the authenticated `tenant_id` and user ID; client metadata cannot replace trusted principal fields; the final merged JSON value is bounded before write.
- **Acceptance and regression proof:** focused backend tests prove tenant/user-scoped locked merge, rejection of oversized or reserved metadata, and route read-after-write behavior; focused frontend tests prove catalog-filtered server metadata and removal of browser-local favorite authority.
- **Evidence ceiling:** source and local focused checks do not prove that a deployed PostgreSQL schema was migrated or that cross-browser persistence works on a packaged runtime.
- **Migration and rollback:** add one non-null JSONB object column with an empty-object default and advance the schema ledger; this individual additive change can leave the column installed and unused, but application rollback remains subject to the whole-schema compatibility checks below.
- **Stop conditions:** stop if employee `workId` is not stably provisioned as the scoped local user, the migration becomes destructive, tenant/user predicates are absent, the final merged value cannot be bounded under lock, or Company Navigation would require Redis or browser storage as durable authority.

## Schema lifecycle and readiness

The supported upgrade command is:

```text
python -m app.schema_migrations apply
```

The runner holds a session-scoped advisory coordinator lock across both phases.
Within the core phase it also takes a transaction-scoped advisory lock, creates
the `schema_migrations` ledger when bootstrapping, applies the versioned core
schema, and records the target version plus SHA-256 checksum as `pending` in the
same transaction. After required checks validate, a separate transaction marks
the target `applied`. A matching applied or pending target and checksum skips
the core SQL; safe repeated invocation does not imply that business-schema changes are additive or
reversible. New hot-table indexes are a separate resumable phase recorded in
`schema_index_migrations`: each is built with `CREATE INDEX CONCURRENTLY`
outside the core transaction, catalog validity is checked before it is marked
ready, and an interrupted or invalid build is safely retried. Concurrent API,
worker, or operator attempts serialize without holding a transaction open
across the concurrent index build. Every committed schema change must advance
`TARGET_SCHEMA_VERSION`; reusing a version with different SQL fails closed on
checksum mismatch.

The migration runner owns a separate autocommit coordinator connection whose
session advisory lock spans the core transaction, Run status validation, target
ledger completion and concurrent-index phase.
Failed or cancelled autocommit setup closes the connection before ownership can
transfer to the runner. Once transferred, the runner always attempts session
close, including when explicit advisory unlock fails. The first failure or
cancellation remains primary; a cleanup failure is raised when no earlier
failure exists. The target ledger checksum and retry semantics remain bound to
the exact schema contract.

Compose runs this command as a one-shot `migrate` service before API and worker
startup. `python -m app.schema_migrations status`, API readiness, and worker startup all
verify the target ledger and index-ledger entries, checksum, critical column
types/nullability, named constraints, and valid/ready indexes. Connectivity or
relation existence alone is insufficient.

The Run status check keeps its inline validated form for a newly created
`runs` table. For an existing table, the coordinator adds the exact named check
as `NOT VALID` under a 2-second lock-acquisition budget and commits that DDL
before scanning historical rows. A separate transaction validates it with the
same lock-acquisition budget and a 30-second statement budget, allowing ordinary
Run writes during the scan. The target schema-ledger row remains `pending`
until validation succeeds; a timeout or invalid historical value leaves the
check unvalidated and the target pending for a safe retry. The migration rejects
an unexpected same-name check and never rewrites existing Run statuses.

Upgrade procedure:

1. Follow the [release runbook](../operations/release-operations-runbook.md)
   under explicit deployment authority. Establish the supported source/target
   versions, inspect their retirement prerequisites and recovery limits before
   applying the migration command. Keeping an old image does not prove rollback
   compatibility. Repeated/concurrent invocation is serialized; it does not
   waive cutover prerequisites.
2. Require a successful `status` result for the intended binary before starting
   API and workers.
3. Deploy the matching API, worker, and frontend commit, then verify `/ready`.

When a schema version adds a new deletion-outbox target, an older worker must be
structurally unable to claim it. Artifact rows retain the historical
`pending`/`processing`/`failed` state namespace. File rows use the disjoint
`file_pending`/`file_processing`/`file_failed` namespace and matching terminal
states. The schema migration rewrites any pre-release file row that used the
shared namespace before validating the target/state constraint. Consequently,
an artifact-only worker's historical claim SQL cannot select a file target and
cannot delete its object bytes without a matching file receipt. New workers
remain dual-read for artifact states while all file transitions stay namespaced.
This is the data-safety fence; worker capability heartbeat remains an optional
availability and rollout-observability layer rather than deletion authority.
Private provider images similarly use `provider_image_pending`,
`provider_image_processing`, `provider_image_failed`, `provider_image_dead_letter`
and `provider_image_deleted`. Their metadata survives Session/epoch removal so
physical deletion retains an owner. Upload reservations are created before I/O
and only promoted with the entry transaction; unresolved uploads retain repeated
cleanup to catch late writes. The shared maintenance loop queues orphaned and
soft-deleted-session images without treating them as public Files or Artifacts.

The core schema includes both additive changes and destructive cutovers:

- Stream-only migration requires stopped old producers and the explicit
  [legacy SSE retirement](../operations/redis-streams-sse-cutover-acceptance.md#explicit-legacy-state-retirement)
  before dropping legacy publication/rebuild relations and `run_events`
  publication columns. The cutover retires old index-ledger contracts, so the
  old binary fails readiness after migration; an image swap cannot undo it.
- The [Run lifecycle](run-lifecycle-boundary.md) retirement removes
  `run_tool_permission_requests` after legacy Run settlement. Readiness requires
  that retired relation to be absent.
- The [Agent Profile persistence](agent-profile-persistence-boundary.md) hard
  cut removes legacy profile columns, compatibility triggers and functions.
  These are committed removals, not unused additive fields.

`app/schema.sql` and `app/schema_migrations.py` own the actual transition and
readiness checks. Migration-runner repeatability and a core transaction's
failure rollback do not make a committed destructive cutover reversible. The
core transaction can already be committed when concurrent-index work fails.

Application rollback is allowed only between supported versions with evidence
that the intended binary accepts the entire installed schema, ledger/index
contracts and persisted data/protocols. Individual additive-column compatibility
is insufficient. Any schema reversal requires separate reversibility evidence;
there is no automatic down migration. Do not recreate retired tables, columns,
or writers, edit checksums, or bypass readiness to make an old image start.
After migration begins, use the release runbook's failure/recovery path: a
schema-compatible corrected package or an operator-authorized restore after
classifying the failure. This document does not establish a verified restore
procedure or authorize deployment or recovery. A checksum mismatch or missing
critical contract remains a stop condition requiring operator investigation.

`run_diagnostics` is an additive Runs-owned relation with one row per
`(tenant_id, run_id)`, a composite Run foreign key, a versioned JSON payload and
monotonic revision. Schema readiness verifies the relation, columns, named
constraints and target ledger before API or Worker startup. New application
versions strip the private carrier from `runs.result_json`; historical result
diagnostics remain read-only through the authorized Runs projection.

OpenSandbox renewal observations add two nullable columns to `sandbox_leases`
under a new core-schema ledger version. The existing platform `expires_at`
retains its lease authority. The columns alone are additive; they do not
establish whole-schema rollback compatibility for older application images. Do
not drop the columns until all renewal-receipt callers are retired; no legacy provider-expiry reader is retained.

If whole-schema compatibility has been established for an artifact-only worker,
stop the file-delete producer before that rollback.
Namespaced file rows remain invisible to that worker, so rollback cannot make it
physically delete them; however, deletion progress stops until a target-aware
worker returns. Drain or reconcile every non-terminal file-target outbox row
before claiming availability is restored. The additive file lifecycle,
typed-outbox columns, and namespaced states may remain installed; dropping or
rewriting them is not a safe application rollback.

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

### Object-deletion ownership and settings

Generic claim, receipt, retry, dead-letter, and operator requeue logic belongs
to the object-deletion boundary. Artifact expiry and ACL reads remain
artifact-owned, owner-requested file admission is file-owned, and the worker
runs one shared bounded loop.

Deployments use only the shared worker settings:

| Setting | Scope |
| --- | --- |
| `OBJECT_DELETE_BATCH_LIMIT` | Shared artifact/file outbox claim batch. |
| `OBJECT_DELETE_MAX_ATTEMPTS` | Shared retry/dead-letter threshold. |
| `OBJECT_DELETE_RETRY_BASE_SECONDS` | Shared retry backoff base. |
| `OBJECT_DELETE_RETRY_CAP_SECONDS` | Shared retry backoff cap. |

The former `ARTIFACT_OBJECT_DELETE_*` environment and Python aliases, and the
artifact-retention fallback for the shared claim batch, are no longer
accepted. This remains a configuration and code-ownership rename only: it does
not change SQL, persisted states, retry semantics, or retention eligibility.
The old `app.artifact_lifecycle_repository` import facade has been removed.
Artifact and file lifecycle callers use the owning persistence modules directly.
Some cross-domain adapter calls remain and are tracked as future architecture
debt; removing this facade does not claim complete layer separation.

Cleanup runs in small worker batches and is retryable. The object-deletion worker
claims one object immediately before processing it, up to `OBJECT_DELETE_BATCH_LIMIT`
objects per maintenance round. The existing phase deadline is unchanged: cancelled
or unknown started work retains its generation and retry accounting, while untouched
objects are not preclaimed and do not consume attempts. Each claim retains the
existing oldest-eligible ordering, `SKIP LOCKED`, and stale-lease rules; no existing
dead-letter rows are automatically requeued.
Candidate selection independently checks the exact target identity, storage key,
and `delete_pending` lifecycle, so a bounded quarantine pass cannot expose an
uninspected invalid target to physical deletion.


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
- Artifact and file targets use disjoint persisted state namespaces. An
  artifact-only worker can continue processing historical artifact rows during
  a rolling upgrade, but its state predicates cannot claim, fail, receipt, or
  requeue file rows. Backlog metrics combine the equivalent states only for
  operator visibility; they do not erase the persisted protocol distinction.
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
- Private Run diagnostics follow the owning Run's approved lifecycle. The table
  has no implicit cascade delete and no independent TTL setting; a future physical
  Run delete must remove its diagnostic record explicitly in the same authorized
  lifecycle transaction and audit that outcome.

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
| Private diagnostics across all Attempts of one Run | 128 KiB |
| Run-event payload | 64 KiB |
| Accumulated run-step payload | 64 KiB |
| Run-event message | 16 KiB |
| Context snapshot payload | 256 KiB |
| Artifact manifest | 64 KiB |
| Audit payload | 32 KiB |
| User profile metadata | 16 KiB |
| Message content | 256 KiB |
| Message metadata | 64 KiB |

Large files and generated outputs continue to use MinIO/S3. Raising a limit
requires a reviewed schema/query impact assessment; it is not a client option.

## Explicit non-goals

This contract does not change single-server port exposure, Docker network
hardening, PostgreSQL/MinIO backup or PITR, Compose CPU/memory limits, disk
isolation, PgBouncer, sharding, replicas, Kafka/ClickHouse, or OpenSandbox
single-host resource isolation.
