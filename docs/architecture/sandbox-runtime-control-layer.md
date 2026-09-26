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
   one current active lease. A callback batch is persisted through the durable
   `(tenant_id, run_id, attempt_id, batch_id)` receipt when the executor supplies
   `batch_id`. Terminal-only answer deltas may span multiple bounded callback
   batches; the receipt is created only after every delta batch and the final
   completion batch are acknowledged. Missing-batch compatibility requests
   remain a migration gap and must be removed only after every deployed executor
   sends batch identities.
   A successful streamed answer returns a versioned `AssistantAnswerReceipt`
   containing only `schema_version`, `message_id`, `delta_count`, `text_length`,
   and `last_delta_event_id`. Its legacy `message` is exactly empty, and failed
   or cancelled terminals cannot carry a receipt. It never carries the full
   answer body. The Worker
   accepts only current-Attempt, strictly ordered v4 rows whose database and
   canonical metadata publication states are both `published`; `pending` remains
   retryable, while inconsistent or `suppressed` publication fails closed. Receipt,
   identity, sequence, count, or length mismatch also fails closed. Legacy
   non-streaming bounded terminal messages use the same stable-source
   `assistant_delta` compatibility shape only when no streamed answer exists;
   obsolete `assistant_final` is retired.
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
   A nonterminal OpenSandbox heartbeat verifies the exact provider identity before
   renewing its remote lifetime. The callback records the SDK's absolute
   `expires_at` as nullable `sandbox_leases.provider_expires_at`, with
   `provider_renewed_at`, under the same active Run/Attempt/lease fence; these
   are provider observations, not substitutes for the platform lease's
   `expires_at` or an authorization grant. If the SDK provides no valid future
   receipt, the callback rolls back with the existing disclosure-safe 503.
   The external renewal and PostgreSQL commit are not atomic: a failed commit
   can leave the provider alive longer than the platform lease.
4. A real-provider release takes the scoped lease row lock, calls provider stop,
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
   (with payload fallback for historical rows) and the native-tool requirement.
   Conflicting attempt projections fail closed. Cleanup uses only verified
   runtime handle fields and derived owned-resource identities, never a
   payload-supplied container or host path.
5. Tenant/run authorization is resolved before any provider call. Provider
   handles are never returned in public payloads.
6. Provider-internal recovery state is observed and reconciled; it is never
   copied into an independently writable business state.
7. Provider stop exceptions are normalized without leaking provider details.
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

## Task workspace and Claude project instructions

Each attempt receives a platform-owned `CLAUDE.md` at the root of its assigned
workspace. Claude Agent SDK runs with the workspace as `cwd` and project setting
sources enabled, so the file supplies the default Simplified Chinese response
instruction. An explicit user language request takes precedence. The platform
rewrites this file when it prepares an attempt, excludes it from artifact
collection, and denies SDK Write/Edit access to it. The release workspace
initializer accepts this exact attempt-root file only as a regular `0444` file;
all other workspace entries remain subject to the owner-writable requirement.

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
platform/runtime state, debug/audit trees, native-tool scratch space, and platform
instruction files remain excluded. An authorized Skill may select a file below its
exact staged `output/` directory, while the rest of the installed Skill stays
private. Missing, unknown, symlink, and non-file entries fail closed. The former
output-directory write allowlist and `outputs/**/delivery/`-only collection rule
are retired together so a permitted write cannot disappear solely because of its
path.

## Native local tool admission

The platform does not duplicate the Claude SDK's parameter schema for these local
sandbox tools. When a real sandbox grants `sandbox_full_local`, tool identity and
workspace boundary remain platform-authorized, while ordinary tool parameter
names and shapes are validated by the SDK/tool implementation. Glob/Grep
workspace patterns remain platform-checked; brace, extglob, character-class,
and question-mark forms fail closed because their expansions can cross private
roots or escape the authorized workspace. Skill identity and
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
5. Add credential-vault provenance and keep default-deny egress. This must be
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
