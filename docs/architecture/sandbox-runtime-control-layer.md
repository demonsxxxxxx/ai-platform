# Sandbox Runtime Control Layer

This document defines the target application authority for acquiring, using,
observing, releasing, and recovering sandbox runtimes. It is a design contract,
not evidence that a deployed OpenSandbox environment has passed acceptance.

## Decision

`SandboxRuntime` is the application-level control authority. Business routes and
workers issue scoped commands to that authority; they do not create provider
leases or mutate a real provider lease into a terminal state independently.

`ContainerProvider` is the provider port. OpenSandbox and Docker SDK details
remain behind that port. OpenSandbox API and Worker calls use the official
OpenSandbox SDK directly; OpenSandbox Server owns sandbox lifecycle and runsc
execution. The stateless model/callback proxy is an egress boundary only and
is not a second application lifecycle.

For current internal-test Releases, native OpenSandbox uses ordinary Docker
`bridge` networking with `runsc` and no OpenSandbox NAT-redirect egress sidecar.
There is no dedicated host guard: tasks may reach routable private, public,
metadata, peer and host addresses. This profile admits only a test deployment
with an explicit internal-test security profile and exact image, Attempt,
callback, proxy and workspace bindings; it makes no production isolation claim.
The production runtime contract still uses an operator-configured dedicated
Docker bridge with host NAT and public Internet access. Its host guard denies
private, link-local, metadata, host and peer destinations and new inbound
connections; the existing model/callback proxy is the sole private task-network
exception. The trusted OpenSandbox host control plane retains access to sandbox
services. IPv6 is disabled on that task network and denied by bridge-scoped host
rules. Both modes keep `network_policy=None`: OpenSandbox's egress sidecar
depends on sandbox NAT support unavailable in gVisor. Signed proof fields record
`network_internal=false`, `default_deny_outbound=false` and the
`host-public-egress-v1` policy subject. This is an admission binding, not a
substitute for observing host firewall enforcement. Previous internal-network
leases remain readable as historical facts and eligible for exact-identity
cleanup; they cannot be acquired, dispatched or renewed under the new policy.
Provider-specific network and SDK details remain behind the existing port.

The published package currently has one internal-test overlay; no new
production package is emitted. Its workspace root and migration source remain
operator values, and OpenSandbox still receives only one authoritative Attempt
directory. The retained production source overlay and host configuration are
not changed or published by this test-profile restoration.

### Change Contract: internal-test bridge restoration

Execution owns the explicit test-only profile admission, Run/Attempt lease
persistence, current-identity dispatch/renewal and exact historical cleanup.
Delivery owns the sole internal-test package, retaining digest-pinned images
and the unchanged Compose project/data volumes. The earlier retirement of
active bridge execution is reversed only for test OpenSandbox on ordinary
`bridge` with `runsc`; no production package is emitted. Existing v0.1.1
Releases and deployed hosts remain unchanged. Superseded tests asserting that
internal-test is always rejected or that a new production archive is published
are replaced with scoped acceptance and rejection tests. Both the current test
and historical stop-only lease paths retain exact identity checks. Active
internal-test leases carry `internal_test_lease_version=active-v1`; default
heartbeat and terminal reconstruction accepts that marker only when the current
test profile, image and runtime subject match. Old rows remain stop-only. No fallback
from governed to test is permitted; invalid profiles fail closed.

The package controller checks the host's actual Docker `bridge` gateway before
pulling or stopping services. The internal-test model proxy must bind only
that private IPv4 address on port 18043 and both API and Worker must point to
it; the callback URL must match the same gateway with the API published on
host port 8020. Lifecycle inputs must supply a valid private IPv4 base URL or
a complete domain/protocol pair. This is a configuration drift gate, not a
production network guard.

Acceptance covers the test bridge and workspace end to end, rejects drift
between host/application network and mounts, and preserves image digests,
RunAttempt scope, model/callback authority, data identity and failure recovery.
Source tests do not establish real Linux/gVisor enforcement or deployment.

The platform owns these durable facts:

- tenant, workspace, user, session, run, and attempt binding;
- admitted image, capabilities, resource policy, Skills, files, network, and
  credential scope;
- the verified provider runtime handle and its observation timestamps;
- execution callback batch receipts and public event projection;
- workspace collection and artifact publication receipts;
- release intent, provider stop outcome, reconciliation outcome, and audit facts.

## Target lifecycle

The target platform state machine is:

| State | Meaning | Allowed next states |
| --- | --- | --- |
| `requested` | Admission accepted and one attempt identity allocated | `provisioning`, `failed` |
| `provisioning` | One fenced owner is acquiring and verifying a provider runtime | `ready`, `failed`, `orphaned` |
| `ready` | Provider handle and required capabilities are attested | `executing`, `releasing`, `orphaned` |
| `executing` | The exact attempt may issue execution and delivery operations | `ready`, `releasing`, `failed`, `orphaned` |
| `releasing` | New execution is denied and provider stop/cleanup is in progress | `released`, `orphaned` |
| `released` | Provider stop and required cleanup have a durable receipt | terminal |
| `failed` | Admission or execution failed with bounded compensation recorded | `releasing`, `released`, `orphaned` |
| `orphaned` | Platform cannot prove the provider resource is absent or owned | `releasing`, `released` |

The existing `sandbox_leases.status = active|released` column remains a
compatibility projection during the first safety slice. It must not be expanded
piecemeal: the target states require a transition ledger, compare-and-swap
generation, timestamps, and reconciliation ownership in one migration.

## Required invariants

1. A real-provider active lease is written only after provider creation and
   runtime-handle verification. Public create cannot mint Docker/OpenSandbox
   rows.
2. Every real-provider lease is bound to one first-class `attempt_id`, and the
   compatibility payload must carry the same value. Historical rows may be read
   through the previous payload field until they expire.
3. Callback authority is the HMAC-bound `(run_id, attempt_id)` token plus exactly
   one current active lease. Callback identity and token authentication share
   one typed reception path. Durable admission and rechecks are owned by
   `sandbox.api.SandboxCallbackAuthority`, assembled in
   `bootstrap.sandbox_callbacks` with the existing Run and lease queries. It
   locks Run before the exact Attempt using the caller's connection and
   transaction, derives tenant from persisted Run identity, and preserves
   session, terminal, Attempt and token-generation fences. The dedicated
   `ProviderSessionAppendIdentity`, `ExecutorTerminalReceiptIdentity` and recovery-operation
   completion identity have separate cancelling-tail paths. Terminal receipts
   require the original unexpired lease, its persisted token, and exactly one
   cancellation generation step while the queue-worker Attempt remains current.
   They retain private batch deduplication and token-usage accounting, but reject
   messages, state patches and ordinary events; they cannot renew execution.
   Recovery completion
   still requires the exact durable started-operation receipt; started, query,
   close, input, context and credential requests cannot request that lease scope. HTTP token parsing and rejection status/detail
   mapping remain in the callback route, as do
   receipt persistence, renewal sequencing and post-commit publication. The MCP
   credential callback uses this same authority and the selected Server's
   platform-recorded lease binding; the [MCP execution contract](mcp-tool-execution.md#principal-credential-lifetime)
   owns its credential refresh and no-store response behavior. The old
   route-owned guards and their misleading Attempt-before-Run name are retired;
   there is no compatibility alias or second validator.
   A callback batch is persisted through the durable
   `(tenant_id, run_id, attempt_id, batch_id)` receipt when the executor supplies
   `batch_id`. Terminal-only answer deltas may span multiple bounded callback
   batches; the receipt is created only after every delta batch and the final
   completion batch are acknowledged. Missing-batch compatibility requests
   remain a migration gap and must be removed only after every deployed executor
   sends batch identities.
   A successful streamed answer returns a versioned `AssistantAnswerReceipt`
   containing only `schema_version`, `message_id`, `delta_count`, `text_length`,
   and `last_delta_event_id`. Its legacy `message` is exactly empty, and failed
   or cancelled terminals cannot carry a receipt or response-file references.
   The executor admits those success-only fields after its SDK capability outcome
   is final; an executor-owned downgrade of a successful runner outcome omits them
   after public delivery drains. The final failure classification, SDK usage,
   native session sequence and bounded private diagnostics remain available to
   the terminal callback and authenticated status probe. This does not repair
   malformed already-failed runner payloads or relax the terminal validator.
   Rejected delivery stays failed; uncertain delivery still suppresses terminal
   publication. No artifact or previously acknowledged answer fact is deleted.
   The receipt never carries the full answer body. The Worker
   accepts only current-Attempt, strictly ordered, committed v4 rows with
   `visible_to_user = true`; retired publication metadata and Redis delivery do
   not govern answer eligibility. Streaming owns
   [receipt reconstruction](../../app/streaming/infrastructure/v4.py) for legacy
   v1 and final answer-part v2 receipts under the
   [current execution-control contract](redis-streams-sse-execution-control.md).
   Receipt, identity, sequence, count, or length mismatch fails closed. Legacy
   non-streaming bounded terminal messages use the same stable-source
   `assistant_delta` compatibility shape only when no streamed answer exists;
   obsolete `assistant_final` is retired.
   Before the executor POST, Sandbox commits the exact tenant/Run/Attempt,
   owner-generation, lease, provider and provider-task identity to the
   Attempt-scoped `sandbox_execution_dispatches` row and places the immutable
   reconciliation context on that same lease. Its `intent`, `accepted`,
   `rejected` and `unknown` values record provider-boundary evidence; they do
   not create another Run or Attempt authority. A lost POST acknowledgement is
   resolved by querying the same lease's authenticated status endpoint with its
   original Run and Attempt identity. A persisted intent is never POSTed again.
   If the status endpoint cannot prove acceptance, the dispatch stays unknown
   and the resource remains available for recovery. The Worker handles that
   typed result as detached work: it acknowledges the queue item while preserving
   the original running Run/Attempt and lease, without terminalizing or releasing
   the Worker binding. The existing stale executor probe reads that lease and may
   persist the task's terminal receipt; uncertainty does not authorize dispatch
   retry. A terminal callback may commit before dispatch acceptance; the
   precommitted context makes the receipt
   immediately eligible for reconciliation. Later acceptance preserves terminal
   executor status and any existing reconciliation claim or outcome.
   After that transaction commits, the runtime emits a second fixed Redis wake
   for a `pending` terminal receipt. Notification has a one-second cooperative
   budget; failure leaves the accepted dispatch and PostgreSQL receipt intact.
   Cancellation during notification propagates without stopping the committed
   receipt's runtime before collection; the local execution-owner stop callback
   honors the same cleanup handoff. `record_sandbox_executor_accepted` records
   post-acceptance timing and the terminal wake; the pre-dispatch intent writer
   owns the initial context write. The uncalled
   standalone `record_sandbox_executor_reconciliation_context` helper is removed;
   it had no production, test, or lazy-export consumer. The context column,
   accepted writer, claim fencing, and terminal reconciliation readers remain.
   A first terminal callback fixes the protocol fields in `executor_terminal_json`
   and normally appends the bounded Runs-owned private diagnostic observation in
   the same PostgreSQL transaction. Diagnostic-only normalization, budget, lock
   wait or write failure is contained by a savepoint with bounded local timeouts;
   the valid receipt/terminal may commit without that observation. Connection,
   savepoint and outer business-transaction failures retain existing retry
   semantics. Receipt retry remains the deduplication authority and does not
   advance the diagnostic revision twice. The receipt is retained for protocol
   protocol recovery; reconciliation may append its bounded `diagnostics` list,
   but cannot replace the first receipt fields. It is not the administrator query store.
   A scoped nonterminal heartbeat may arrive while the active, unexpired lease
   is still `pending`, before its accepted observation commits. Heartbeat and
   provider renewal permit only `pending`, `accepted` and `running` executor
   states; terminal executor states cannot be reopened by a late heartbeat.
   A nonterminal OpenSandbox heartbeat captures the current Run/Attempt/lease
   identity in a short transaction, then verifies and renews the provider outside
   database locks. A second short transaction rechecks the same immutable lease
   identity and active owner before recording the receipt. Remote renewal has an
   eight-second total ceiling, below the executor callback ten-second budget. The callback records the SDK's absolute
   `expires_at` as nullable `sandbox_leases.provider_expires_at`, with
   `provider_renewed_at`, under the same active Run/Attempt/lease fence; these
   are provider observations, not substitutes for the platform lease's
   `expires_at` or an authorization grant. If the SDK provides no valid future
   receipt, the callback records no renewal receipt and returns the existing
   disclosure-safe 503. Empty artifact-validation phases and the redundant
   running-at-99-percent callback are retired; the actual terminal callback
   follows the public delivery drain.
   The external renewal and PostgreSQL commit are not atomic: a failed commit
   can leave the provider alive longer than the platform lease.
   Executor terminal completion has two supervised slots per Worker process,
   each claiming one row only after capacity is available. Redis initialization
   and XREAD run independently of PostgreSQL scans. A retained cursor and an
   immediate scan after cursor initialization close the handoff race; a two-second
   fallback scan also runs without notification. Backlog progress replenishes
   free slots immediately. Terminal retry uses the persisted
   `executor_reconciliation_retry_not_before`, with the existing 1–30 second
   attempt-count backoff. Eligible rows are ordered by terminal receipt age and
   ID, so fresh pending work cannot indefinitely outrank a due retry.
   Receipt-less suspect probes retain their separate 30-second idle schedule.
   A probe batch that persists receipts yields and immediately claims the next
   batch, waking completion without Redis. Empty, retry-only, failed and timed-out
   probes retain idle backoff.
   Once the exact Run has a committed terminal outcome, completion durably hands
   its claim to `cleanup_pending` and frees the completion slot. Cleanup drains
   its own backlog in one supervised slot, with local wake and two-second polling
   for another process's handoff. Failed stops have a persisted 30-second retry
   deadline. Cleanup uses short claim/finalize transactions around provider I/O;
   only `stopped` or authoritative `not_found` may release the lease. The cleanup
   slot remains owned through a cancellation-resistant stop, and shutdown joins
   all owned tasks. These are cooperative deadlines, not hard termination.
   Cleanup takeover uses a 300-second stale claim; repeated stop remains safe
   only on the existing verified immutable resource identity and idempotent
   provider deletion contract. Run/Attempt/lease and claim-token fences remain.
4. Cleanup application ownership is `sandbox.api.SandboxRuntimeCleanup`,
   implemented in `sandbox.application.runtime_cleanup` and assembled by
   `bootstrap.sandbox_cleanup`. Its verified handle reconstruction policy lives
   in `sandbox.application.runtime_handle`; composition supplies the existing
   lease type, signed-proof checks, settings, repositories and event writer.
   Application code does not import HTTP routes, concrete providers or database
   adapters. Worker maintenance, executor reconciliation, callbacks and HTTP
   entrypoints all consume this Sandbox operation boundary. The former
   `routes/sandbox_runtime_cleanup.py` implementation is retired, with no import
   compatibility alias or external wire contract.
   Owner and administrator cancellation of an active Attempt records intent and
   leaves the original executor responsible for bounded SDK drain. Its exact
   provider must stop before private workspace save and the atomic Context/Run
   terminal transaction; lease release follows that transaction's terminal facts.
   A dispatch response still in flight retains the same Attempt's observation
   responsibility, including a verified durable terminal handoff after endpoint
   loss. Routes do not immediately kill/release an active cancelled writer.
   Direct cleanup of an already terminal or never-started Run retains the shared
   stop/release settlement: stop outside its settlement transaction, release
   successful leases grouped by their Run, record the actor-scoped outcome, then
   report any partial stop failure after commit. Authorization, queue/publication
   ordering, 502/503 mapping and owner/admin event distinctions remain unchanged.
   Persistence failure takes precedence over provider failure.
   The following concurrency mechanisms are intentionally distinct: single-lease
   release and expiry cleanup retain stop-under-lock; terminal reconciliation
   cleanup keeps its claim, unlocked bounded stop and fenced receipt transactions. Sharing the
   cancellation settlement does not replace these mechanisms or their retry,
   timeout, signed-handle and historical stop-only compatibility rules.
5. A real-provider release takes the scoped lease row lock, calls provider stop,
   and marks released in that transaction. Concurrent release waits and then
   observes the terminal row instead of issuing a duplicate stop. Stop failure
   leaves the lease non-terminal and records a cleanup failure for retry or
   reconciliation. This is the initial stop-under-lock compatibility mechanism,
   not a target requirement to hold database locks during unbounded provider
   I/O. Replacing it requires a reviewed operation claim, immutable resource
   identity, out-of-transaction provider effect, stale-receipt rejection and
   crash recovery. Until that replacement is activated, do not merely move
   `stop` out of the lock. See [runtime convergence](runtime-convergence.md).
   Expired-runtime cleanup owns its selection, stop, release and failure-audit
   transaction. Partial provider failure commits the successful releases and
   failure audits on the connection holding the candidate locks before raising
   the cleanup error. Callers invoke it before opening their listing or DB-only
   cleanup transaction; a second connection must not compensate rows still
   locked by the first. Cancellation or a database write/commit failure rolls
   back that transaction and leaves the leases eligible for retry.
   Restoring a Docker cleanup handle preserves the authoritative attempt binding
   (with payload fallback for historical rows), the native-tool requirement,
   signed egress proof and requested image identity for control-channel recovery.
   Conflicting attempt projections fail closed. Cleanup uses only verified
   runtime handle fields and derived owned-resource identities, never a
   payload-supplied container or host path.
6. Tenant/run authorization is resolved before any provider call. Provider
   handles are never returned in public payloads.
7. Provider-internal recovery state is observed and reconciled; it is never
   copied into an independently writable business state.
8. Provider stop exceptions are normalized without leaking provider details.
   An authoritative OpenSandbox SDK absence response during connection,
   `get_info`, or deletion returns `not_found` and clears only the matching
   provider tracking. The existing claim-fenced release path accepts that
   result. Untyped "not found" text, timeouts, other provider failures, and
   runtime identity mismatches remain stop failures and retain the lease.
   Expiry compensation and admin orphan-cleanup failures write tenant-scoped
   audit outcomes while the failed lease remains a reconciliation subject.
   A lost executor probe keeps the public `sandbox_executor_lost` receipt and
   stores only classified probe stage, exception type and validated SDK/HTTP
   facts in Runs-owned private diagnostics. A retry stores a fixed safe lease
   error code instead of a raw provider exception; absent executor diagnostics
   never synthesize an `unsupported_schema` observation.

`attempt_id` is the first ownership fence in the initial slice. It does not yet
replace a general monotonically increasing fencing generation for provider
commands. That generation belongs in the target transition-ledger migration.

## Durable cancellation delivery

Runs stages an exact Sandbox cancellation intent in the same transaction as
owner/admin cancellation. After commit, the shared post-processor attempts
executor cancellation before stream publication. The independent Worker
reconciler phase also consumes due intents every two seconds, including after
the request process exits. Heartbeat staleness and the 30-second suspect probe
cadence remain unchanged. Redis is not the cancellation delivery queue.

`SandboxCancelDeliveryService` owns delivery; `bootstrap.sandbox_cancel_delivery`
composes PostgreSQL and the provider's authenticated control endpoint. The lease
records `none`, `pending`, `claimed`, `retry` or `accepted`, a frozen execution
generation, claim token and retry/acknowledgement timestamps. These facts are
control delivery evidence, separate from SDK completion, business settlement and
resource release. The original queue-worker generation may advance exactly once
for cancellation while the lease retains its original generation.

Claim and writeback use short Run → lease → Attempt transactions. Provider I/O
runs outside these locks, with a 25-second cooperative batch budget and a
five-second HTTP timeout. The batch includes fresh provider endpoint identity
and network inspections; those can exceed ten seconds on Docker Desktop before
HTTP begins. A new transaction rechecks current scope, Attempt,
lease validity and claim before recording ACK or retry. Claims stale for sixty
seconds can be reclaimed. Retry backs off to at most 300 seconds; terminal
receipts and invalid authority clear unfinished claims. Bounded keyset scans
skip occupied rows and continue across rounds; their process-local position is
only a fairness hint. Restarting a scanner does not change durable ownership.

An exact authenticated cancel received before task dispatch is latched by the
executor. Later dispatch returns an unaccepted-input cancellation receipt without
entering the SDK. Its receipt deadline begins when dispatch processing starts.
Duplicate cancel does not interrupt the original writer's bounded mirror drain.
An ACK alone never terminalizes a Run, releases a resource or creates tool success.

The integration schema `2026.10.30.1` adds these fields and guards while preserving
main, lifecycle and conversation migration history. Apply it before matching
API/Worker/executor binaries. Drain incompatible binaries and in-flight tasks
before a binary rollback; old consumers cannot drain the new cancel intents or
read the combined canonical/per-Attempt continuation layout. No operator cutover
marker is activated by this migration. Source and local database tests do not
establish provider timing or real-environment acceptance.

## Terminal completion compatibility and retirement

Schema `2026.10.13.1` adds the nullable retry deadline and `cleanup_pending`.
Existing terminal retries retain `updated_at + min(30, max(1, attempt_count))`
as their first deadline. Probe retry retains its separate heartbeat/attempt clock.
The accepted writer cannot reset either a deadline or a cleanup handoff.
The concurrent-index ledger advances with this contract. The existing
`idx_sandbox_leases_executor_reconcile` is rebuilt to order by receipt age and
ID for both active and historically released receipts; its active-only,
status/updated-at definition is retired. A separate partial retry-deadline index
supports the next-due query without scanning finalized history. Both indexes
have one concurrent migration owner and exact readiness catalog contracts;
they are not duplicated in the core schema's static index registry.

In Attempt-owned settlement, a new claimant rebinds an existing immutable
stop/save checkpoint to its current Runs claim under the same source locks.
It does not reconnect the stopped producer. Finalization still rejects the old
claim and commits Context, Run/Attempt and lease facts atomically, including
when a terminal Run only needs its lease finalized. Both compatibility modes
record cleanup as finalized in this transaction. A checkpoint created during
the current collection pass is not handed to the cleanup queue again.
An Attempt-owned settled result also acknowledges an exact lease already
claimed or finalized by cleanup without resetting that owner's token or progress.

The inline terminal provider-stop helpers and the claimed-to-finalized release
writer are retired from `executor_reconciler.py` and `sandbox_leases.py`, together
with their former inline-stop tests. Terminal completion tests now assert durable
handoff; Sandbox cleanup tests own stop, timeout, lost-claim and finalization.
The serial completion loop, fixed pending priority, terminal retry derived from
arbitrary lease updates and Redis-dependent 30-second scan wait are replaced.
The existing `cleanup_failed_*` API names remain as the compatibility owner for
persisted legacy `failed` cleanup rows and the new handoff; both use one claim,
stop and finalize implementation. Their removal requires absence of legacy failed
rows and migration of all current callers, not a second cleanup implementation.
No new public endpoint, queue ledger, SDK execution path or environment setting
is introduced. Existing test files remain in their current CI shards; the real
PostgreSQL/Redis handoff regression belongs to `test_sandbox_persistence.py`.

The schema readiness gate requires the new target. Stop old writers before
migration and start only matching binaries under the release runbook. Mixed-version
writers are unsupported. Binary rollback requires first draining or explicitly
mapping `cleanup_pending` to the legacy cleanup owner while retaining unfinished
leases; simply starting an older Worker cannot consume the new state.

## Provider port

The existing provider operations map to the control layer without exposing an
SDK to routes or workers:

- `create_or_reuse` -> acquire;
- `validate_for_dispatch` and readiness -> get/verify;
- `stage_workspace` and execution submission -> prepare/execute;
- `collect_workspace` -> collect;
- `stop` -> release;
- `list_runtime_containers` and `cleanup_orphan_containers` -> reconcile.

Renaming these methods is not a correctness requirement. Consolidating their
invocation and durable receipts behind the application control authority is.

### Docker asynchronous I/O boundary

The Docker provider keeps a valid published executor endpoint when available.
An internal bridge may have no published host port even when one was requested.
In that case the provider resolves port 18000 on the executor's private IPv4
address only after fresh Docker inspection verifies the exact Run/Attempt
container, owned internal bridge, API witness, two-member topology, and matching
container/network attachment addresses. The network must disable masquerading;
Docker's explicit IPv4-enabled/IPv6-disabled defaults are accepted, while other
options and IPv6 remain rejected.

Callback witness lookup filters by API release role, release owner and source
commit at the Docker daemon, then reloads matching candidates before checking
identity, health and uniqueness. Attaching that witness is idempotent only for
the exact network ID and one `api.sandbox.internal` alias. A concurrent duplicate
endpoint response must name the same API container and network and pass a fresh
attachment check; other connection failures deny admission. These checks retain
the same two-member topology and cleanup ownership requirements.

The optional Docker Compose overlay shares the API network namespace with the
trusted Worker so both can reach each Attempt's internal bridge. It removes the
Worker's separate host aliases and starts it after the API is healthy; Compose
API replacement also restarts the dependent Worker. API and Worker remain
separate processes, and the executor remains in its own container and network
namespace. This coupling belongs only to the Docker overlay. Operators must
drain active Runs/Attempts/leases before replacing the API, whose container
identity is part of the existing callback proof, and recreate the dependent
Worker when replacing the API outside Compose. Dependency restart does not
provide in-flight recovery across API container replacement.

Recovery verifies the current-key signature and immutable Run/Attempt, image,
lease, network and callback binding. Docker recovery reads the proof from the
persisted top-level `governed_egress_proof`; runtime labels are filtered before
storage. Image subjects come from the inspected, exact-owned container and must
match that signed proof. Missing proof prevents private control while preserving
historical resource cleanup under its existing ownership checks.
The signed launch-time Skill/MCP scope is
retained, because a reconciliation request is not a new capability admission.
Proof expiry does not prevent status/cancel of that exact owned executor;
create, reuse and dispatch still require a fresh admission proof. Key rotation
revokes control identity, and invalid signatures or scope/ownership drift fail
closed. These checks retain the caller's durable reconciliation claim and do not
replace Attempt authority or grant execution permission.

Docker's synchronous SDK lifecycle runs on provider-owned worker loops rather
than the application event loop. The provider has eight lifecycle slots, eight
probe slots, and two independently reserved cleanup slots; executor queues do
not grow beyond those admissions. Request cancellation or an await deadline does
not return a slot until the underlying thread has actually finished. Existing
SDK and readiness-stage timeouts are retained, including the final bounded HTTP
probe allowance; startup is not assigned a shorter aggregate timeout.

Create, dispatch validation, stop, and orphan cleanup share a per-provider,
in-process run claim. Orphan listings only nominate candidates: cleanup skips
busy runs and holds the same atomic claim through fresh identity/status readback
and removal. A native sidecar is rechecked against a fresh same-scope primary
listing, and a bridge's current membership is reloaded under the claim. Therefore
a startup that finishes after the initial orphan listing cannot turn that stale
snapshot into deletion authority. Different runs retain independent cleanup
capacity. A cancelled
create checks cancellation after blocking mutation returns, before issuing the
next mutation, and compensates only the exact owned attempt resources. The claim
remains held through late worker completion and compensation, including a
successful create whose result the caller never accepted. Cancellation waits at
most the existing SDK timeout for compensation; a still-blocked worker retains
its capacity and ownership until it can settle. Typed cleanup failure retains a
tracked reconciliation obligation, while unrelated worker errors cannot replace
caller cancellation. These process-local claims do not replace database attempt
fences or establish ownership across controller processes.

The legacy provider delegates this boundary and its existing tracked-lease and
resource-cleanup helpers to `platform.sandbox.docker_operations`, the Docker SDK
thread-isolation adapter. Docker's previous application-loop SDK calls and
shared-default-executor probes are retired; OpenSandbox keeps its existing async
SDK lifecycle. No provider ownership checks or durable release semantics change.

## Native MCP file access

The locked Claude CLI owns decoding and saving standard MCP resource results.
Its project directory is pinned with `CLAUDE_CODE_PROJECT_DIR_NAME=ai-platform`;
files remain in `.claude-config/projects/ai-platform/{sdk_session_id}/tool-results`.
The existing provider session identity and Attempt workspace bind this location.
Platform session-store loading and resume decisions retain their existing owner.

Docker native filesystem version 3 exposes only that bound session's result
directory through a read-only bind. An empty, traversable configuration mask
provides its parent path; credentials, transcripts, other sessions, and other
runtime-private roots stay hidden. Directory preparation rejects symlinks and
non-directories. Cached native filesystems from earlier versions fail existing
reuse validation and must be recreated through the provider lifecycle.

SDK path admission and search-result filtering permit only the current provider
session's native result files, including lexical and resolved-path checks. Writes and artifact collection still reject this
runtime root. The Agent uses its authorized Bash or Skill to parse the file and
can create a deliverable in an ordinary workspace directory for explicit attachment.
The platform maintains the mount and access boundary; the CLI owns file bytes
and the Agent owns document processing. Files-owned UI import/download and
platform-uploaded attachment staging keep their separate contracts.

## Task workspace and Claude project instructions

Each attempt receives a platform-owned `CLAUDE.md` at the root of its assigned
workspace. Claude Agent SDK runs with the workspace as `cwd` and project setting
sources enabled, so the file supplies the default Simplified Chinese response
instruction. An explicit user language request takes precedence. The platform
rewrites this file when it prepares an attempt, excludes it from artifact
collection, and denies SDK Write/Edit access to it. The release workspace
initializer prepares only the runtime root and platform namespace directories
through `attempts/{attempt_id}`. It does not traverse ordinary Attempt contents,
change their permissions, or run a compatibility ACL migration. No-follow
identity/device checks protect platform namespace directories; after switching
to the runtime identity, a root write/read/delete probe verifies availability.
The networkless, read-only initializer retains `CHOWN`, `DAC_READ_SEARCH`,
`FOWNER`, `SETUID`, and `SETGID` only for namespace preparation and privilege drop.
New Skill materialization owns its directory and file modes. Skills have one
native discovery location, `.claude/skills`.

Skill writes are allowed anywhere else in the assigned workspace. The protected
roots remain `inputs/`, `.claude/`, `.ai-platform/`, the runtime configuration
roots, and the OpenSandbox attempt sentinel. Lexical and resolved paths must both
remain inside the workspace, which preserves traversal and symlink-escape
protection.

Artifact collection never enumerates the workspace. The Agent explicitly selects
each final deliverable with the private `attach_file` tool. The controller derives
the exact Attempt workspace from the authoritative lease, validates the ordered
path allowlist through no-follow descriptors, and publishes an immutable snapshot
outside the sandbox-mounted workspace before artifact storage reads it. OpenSandbox
does not download response files; its remaining Files API use is limited to the
bounded lease-sentinel control readback. A selected file may be in an ordinary
workspace directory such as `output/`, `tasks/`, `artifacts/`, or `review/`; inputs,
platform/runtime roots, root debug/audit trees, native-tool scratch space, and
platform instruction files remain excluded. Ordinary nested directories named
`runtime` are readable and deliverable. Task-owned `logs/` is also readable and
deliverable: platform logs live beside the Attempt workspace, not inside it.
An authorized Skill may select a file below its
exact staged `output/` directory, while the rest of the installed Skill stays
private. Missing, unknown, symlink, and non-file entries fail closed. The former
output-directory write allowlist and `outputs/**/delivery/`-only collection rule
are retired together so a permitted write cannot disappear solely because of its
path.

The host-bind path resolution, sentinel read, and selected-file snapshot routines
are owned by `app.sandbox.domain.host_bind` and exposed through `app.sandbox.api`.
The provider composes the snapshot through `app.bootstrap.sandbox_snapshot` and
`app.sandbox.infrastructure.host_bind_snapshot`, using the existing bounded
storage admission and default executor. No-follow traversal, selected-file
allowlist, flock, stable-copy/hash checks, fsync and atomic publication remain
inside the same synchronous domain operation. Other host-bind reads still call
the public boundary directly. Every opened traversal descriptor is
owned immediately and closed exactly once on success, metadata rejection, or
filesystem inspection failure, including rejected roots and nested directories.
Snapshot cancellation waits for a submitted physical operation to finish before
propagating the original caller exception and releasing the reconciler claim.
Repeated cancellation cannot shorten this join. Storage capacity is released on
the owner loop after physical completion, including a cancelled asyncio waiter;
worker shutdown gathers maintenance owners before closing clients. Admission
failure has no physical job to join. The snapshot opts out of the helper's
request wait budget, preserving the original physical-completion semantics;
filesystem operations are not forcibly interrupted. Shared storage slots may
therefore delay other storage work. This removes event-loop blocking, not the
underlying copy/hash/fsync work, and does not establish production latency.
Durable Attempt snapshot retention is separate; this path does not delete
historical directories after cancellation.

The artifact collector publishes selected files as opaque bytes. Agent/Skill
execution owns document format and quality checks; the collector does not unpack
DOCX or parse XML. Path confinement, file-count, raw per-file/total-byte limits,
artifact authorization and storage receipts remain mandatory. Declared required
artifact types are checked once by terminal completion, after collection.

The previous recursive workspace
manifest and file-upload helpers have been removed from both modules.

## Native local tool admission

Docker native commands share the ordinary Attempt workspace through a writable
mount. Inputs, platform instructions and staged Skills have nested read-only
mounts; SDK runtime-private roots are hidden by empty read-only mounts. IPC lives
outside the workspace at `/run/ai-platform-native`, with a root-owned directory
and socket. The native launcher binds the listener using startup privileges,
then permanently drops groups, UID/GID and capabilities before serving commands.
The executor connects through a read-only IPC mount. After stopping the native
container, a scoped cleanup helper removes its root-owned socket; cleanup failure
keeps the existing lease retry obligation. Older native filesystem layouts are
rejected at reuse and retired with their runtime pair.

The native command owner starts one cleanup task after command execution ends.
It joins the existing process-group termination, same-UID sweep and both bounded
output readers before returning or releasing the request's command lock, even
under repeated command/request cancellation. Readers remain active during
process termination; success and timeout drain their bounded output, while
cancellation or failure cancels and gathers unfinished readers. Cancellation
remains the caller outcome after cleanup; other cleanup failures stay sanitized.
Signal scope and termination budgets are unchanged. A failed UID sweep is not
proof of process quiescence, and the existing subsequent-admission policy remains
unchanged; this ownership fix does not add a failed-sidecar admission latch.
Direct cancellation of the cleanup task (for example during global loop teardown)
is a different boundary: it can interrupt cleanup. The command still preserves
an already observed caller cancellation, but does not claim survival of global
all-task shutdown or completed process cleanup in that case.

`tools/native_tool_filesystem_smoke.py` exercises the built image's actual mounts,
private-path mutation attempts, consecutive commands, privilege drop and socket
cleanup in the backend image CI check. Unit configuration checks alone do not
establish these kernel boundaries.

The platform does not duplicate the Claude SDK's parameter schema for these local
sandbox tools. When a real sandbox grants `sandbox_full_local`, tool identity and
workspace boundary remain platform-authorized, while ordinary tool parameter
names and shapes are validated by the SDK/tool implementation. Glob/Grep
workspace search scopes remain platform-checked; brace, extglob, character-class,
and question-mark patterns use native tool semantics within that scope. Every
returned path still passes the workspace and private-root boundary. Safe native
search fields retain SDK semantics; when platform filtering or output budgets
remove results, complete totals are omitted and Glob is marked truncated rather
than claiming that the visible subset is complete. Grep pagination parameters
remain intact, and global totals are omitted for filtered or paginated results.
Skill identity and
object constraints, platform context tools, external MCP schemas, and the
Docker-native command proxy's command/timeout limits remain platform-owned.
This contract does not claim support for background Bash jobs; a local Bash
request with `run_in_background=true` currently fails closed because no
RunAttempt-bound monitor owns that process. Its lifecycle must be separately
bound to the RunAttempt and proved before that feature is enabled.

`attach_file` is a private in-process MCP tool with an exact platform-owned
parameter contract and normal tool lifecycle evidence. It records file selection;
it does not upload bytes, scan directories, or replace the assistant answer. The
SDK's non-error terminal `ResultMessage` remains completion authority, while its
ordinary `result` text and the ordered attachment selections remain separate
terminal fields.

A policy-only denial of an optional tool is not an executor failure when the SDK has
already produced a final answer: the executor returns the answer with
`tool_outcome=denied` and `tool_outcome_code=tool_permission_denied`. A required
capability declaration, lifecycle/receipt mismatch, missing SDK terminal,
or executor control-plane error remains fail-closed and terminally failed. This
keeps recoverable tool outcomes separate from the Run outcome without adding a
second Run status vocabulary.

## Delivery slices

The first slice closes immediately unsafe competing-writer paths:

- reject public real-provider creation;
- require a complete verified runtime handle and first-class attempt when a real
  lease is persisted by `SandboxRuntime`;
- stop a real provider before explicit release and keep failures recoverable;
- persist failure outcomes for explicit release, user/admin cancellation,
  expiry compensation, and on-demand admin orphan cleanup;
- bind cleanup failures to their concrete lease and surface audit persistence
  outages explicitly instead of silently claiming a durable outcome;
- use the existing event-batch receipt from executor callbacks;
- give every executor callback a restart-namespaced batch identity that remains
  stable when the same serialized callback is retried.

The next correctness slices are:

1. Persist the target transition ledger with CAS generation and a reconciler
   owner lease.
2. Attest bounded CPU/memory/time and browser capability before `ready`.
3. Add idempotent stage/collect and artifact-publication receipts, including
   object-store orphan compensation.
4. Schedule provider reconciliation and expose orphan, cleanup, capacity, and
   callback-delivery metrics.
5. Add credential-vault provenance and retain host, private-network and peer
   isolation while permitting task Internet access. This must be
   designed with the selected provider topology rather than inferred from an SDK
   feature name.

Warm pools, snapshots, and pause/resume are performance features. They do not
precede lifecycle, ownership, delivery, and reconciliation correctness.

## Migration and rollback

The first schema change is additive: nullable `sandbox_leases.attempt_id` plus an
exact-attempt index. New real-provider writes require it; reads temporarily fall
back to `lease_payload_json.attempt_id` for historical rows. Rollback removes the
index and column only after reverting readers and the real-provider write guard.

Runtime acceptance remains mandatory. Source tests prove ordering and
fail-closed contracts but do not prove provider readiness, network enforcement,
cleanup, or orphan recovery on a deployed host.

## Cross-component acceptance

Use SBX-01, SBX-02, TX-01, RUN-03 and CB-01/CB-02 in the
[system matrix](../acceptance/system-architecture-matrix.md) for failure and
handoff coverage. These are proposed test scenarios, not passing evidence.
The existing resource lifecycle, token scope and compatibility requirements
remain in force; execution authorization, dispatcher ownership and cleanup
claims must not be collapsed into a universal generation.
