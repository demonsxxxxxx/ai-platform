# Sandbox Runtime Control Layer

This document defines the target application authority for acquiring, using,
observing, releasing, and recovering sandbox runtimes. It is a design contract,
not evidence that a deployed OpenSandbox environment has passed acceptance.

## Decision

`SandboxRuntime` is the application-level control authority. Business routes and
workers issue scoped commands to that authority; they do not create provider
leases or mutate a real provider lease into a terminal state independently.

`ContainerProvider` is the provider port. OpenSandbox and Docker SDK details
remain behind that port. The OpenSandbox Gateway may keep its own internal
`uncertain_create/reconciling/cleanup_pending/deleted` recovery states, but those
states are provider observations rather than a second application lifecycle.

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
   `batch_id`. Missing-batch compatibility requests remain a migration gap and
   must be removed only after every deployed executor sends batch identities.
4. A real-provider release takes the scoped lease row lock, calls provider stop,
   and marks released in that transaction. Concurrent release waits and then
   observes the terminal row instead of issuing a duplicate stop. Stop failure
   leaves the lease non-terminal and records a cleanup failure for retry or
   reconciliation.
5. Tenant/run authorization is resolved before any provider call. Provider
   handles are never returned in public payloads.
6. Provider-internal recovery state is observed and reconciled; it is never
   copied into an independently writable business state.
7. Provider stop exceptions are normalized without leaking provider details.
   Expiry compensation and admin orphan-cleanup failures write tenant-scoped
   audit outcomes while the failed lease remains a reconciliation subject.

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
