# Runtime Convergence Contract

Status: proposed implementation direction. This document identifies cross-owner
constraints and migration order; it does not assert implementation, amend live
wire bytes, approve a schema change, or authorize deployment. Detailed state
machines remain with Runs, Sandbox and Streaming. Track work status in issue/PR.

## 1. Independent implementation slices

The unit of delivery is one observable invariant, its smallest complete caller
closure, a falsifiable owning test, and an explicit retirement result. A slice
records before/after behavior, accepted inputs, owner/fence, transaction scope,
I/O, failure outcomes, compatibility, rollback and acceptance IDs. Route/SDK/SQL
moves that preserve behavior are separate from changing that behavior.

## 2. API progress and object I/O

Put synchronous S3 and expensive parsing behind a bounded execution boundary.
Limit concurrency and retained bytes, reuse lifecycle-owned clients, and set
connection/read/retry budgets. Do not detach unbounded threads or treat caller
cancellation as proof that a synchronous operation stopped. A permit remains
accounted for until the actual work ends; enforce a process boundary for work
that needs hard termination. Keep callback/SSE responsiveness independently
observable. Bucket provisioning belongs to initialization, not each object put.

Accepted metadata and object bytes must reconcile after ambiguous commits. Do
not delete bytes merely because a metadata-commit response was lost. Reuse the
file/object lifecycle boundary for confirmed compensation and explicitly track
pre-record orphan work. Public access still re-proves exact scope and eligibility.
Acceptance: IO-01, IO-02, DATA-01.

## 3. External side effects and transactions

Target sequence: a short owning transaction validates and records a claim/intent;
the external operation runs without held business-row locks; a short transaction
compares the exact claim/generation and records its outcome. A lost or stale
claim cannot receipt a newer operation. Provider success with uncertain database
commit remains a reconciliation subject. A failed or unknown stop never means
`released`.

The current Sandbox stop-under-lock rule is a compatibility mechanism. Keep it
until an explicitly reviewed replacement supplies operation identity, fenced
receipt, recovery, and mixed-version safety. A database fence alone cannot stop
an already-issued remote call: either the provider/gateway checks a scoped token,
or the operation is idempotent on an immutable resource and takeover waits for
a proven safe boundary. Record this choice per operation. Do not claim exactly
once for an external effect on the strength of a PostgreSQL CAS alone.

Context object retrieval similarly uses an authorized bounded selection, I/O
outside the long-held Run lock, and a final authorization/fence check before
release of the result. No unlock shortcut may turn a cancelled or foreign Run
into an authorized reader. Acceptance: TX-01, TX-02, SBX-01, SBX-02.

## 4. Recovery scheduling

Database eligible work is authority. Notifications only reduce latency.
After a successful claim/processing pass, continue within a bounded batch/time
budget while eligible work exists; wait only after a proven empty pass. Close
the scan-to-wait race with a retained notification cursor or equivalent observed
handoff, retaining periodic bounded scans for lost notifications.

Separate critical recovery/publication from bulk retention/cleanup scheduling.
Each phase has a deadline, resource budget, next retry and visible last progress.
Timeout handling must not leave unbounded work while launching replacements.
Avoid startup waiting for all bulk cleanup before useful Worker slots start.
Use one supervised lifecycle for count 1 and count N. Initially reuse the Worker
process; independently deployed maintenance is a later packaging decision.
Acceptance: RCV-01, RCV-02, RCV-03, RCV-04.

## 5. Attempt and Sandbox ownership

Finish the existing RunAttempt migration; do not create another execution ledger.
Classify each operation by scheduling owner, execution authorization and provider
resource claim. A scheduling handoff does not automatically revoke a still-valid
Executor. Expiry/revocation of execution authority must fence new effects and
state mutations. All callback, renewal, cancel, collection, terminalization and
release paths declare the full applicable identity and expected state.

Use existing terminal observations and durable claims for asynchronous completion.
Old observers may not overwrite a new authority. Distinguish safe idempotent
replay from permission to re-execute a tool. Retry/copy continue to follow their
own admitted Run semantics. Acceptance: RUN-02, RUN-03, SBX-01, SBX-02.

## 6. Callback delivery and publication

One ordered worker owns runner-event callback network delivery. Adjacent projected
text may batch without rewriting event identity. A non-text or terminal barrier
waits for prior receipt, with an explicit completion result; local queue admission
must not be called durable acknowledgement. Freeze retry payload and batch ID.
Bound queue count, text bytes, serialized bytes and actual queue age separately.
A barrier ends optional batching delay but never overtakes an in-flight receipt.
One absolute close budget covers retries, drains and cleanup; unresolved effects
remain unknown and recoverable at expiry.

A proposed callback response fast path returns the exact persisted receipt after
its commit and wakes the existing durable publisher. It must not spawn a fragile
request-owned background publisher, weaken Tool receipt barriers, or replace
indexed retry with notifications. Activate only after the protocol owner verifies
that the acknowledgement denotes durability rather than Redis visibility.
Remove redundant admission work only after same-authority retry/restart coverage.
Acceptance: CB-01, CB-02, CB-03.

## 7. Client state and protocol ownership

Validate the public frame, bind its current Run/incarnation/connection owner,
apply it to a pure state transition, and atomically commit message state with its
accepted progress. Rendering may lag; requestAnimationFrame and React updater
execution must not decide durable acceptance. Distinguish invalid, duplicate,
deferred and applied outcomes; only validated events may wait for terminal
hydration. Semantic and transport cursors are different, and wall-clock time is
not an event-order authority.

Use one canonical reducer for live events and normalized legacy history. Generate
structural runtime validation from the schema or test equivalence automatically;
retain explicit semantic checks for cross-field bindings. Do not maintain
independent hand-written protocol field registries indefinitely. A durable
snapshot/stream anchor must represent a proven consistent cut under concurrent
writes. Keep current terminal hydration and active-successor restrictions.
Acceptance: SSE-01 through SSE-06.

## 8. Package and API convergence

Route transports delegate to complete existing domain use cases. Bootstrap owns
construction. Sandbox lifecycle calls pass through its application boundary;
provider details remain behind the port. Preserve lock order and the exact
transaction context across owner ports. Do not replace the global repository
with a generic platform service or move every private helper into `api.py`.
Retain Engine-neutral input and public events; keep SDK objects inside the Engine.
Acceptance: OWN-01, OWN-02, CTX-01, AUTH-01, AUTH-02.

## 9. Order and exit

First repair API progress and recovery liveness. Then migrate the specific
external-I/O transactions and callback cleanup. Close attempt/resource fences
before increasing recovery concurrency. Move complete use cases and remove old
writers; then converge the client and schema validation. Capacity, retention and
rollout claims require their dedicated evidence, not a large cleanup test run.

Every activated slice lists the deleted implementation or the remaining named
consumer and removal condition. Keep public compatibility commitments and
immutable historical records. Restore reviewed application images only within
the accepted schema/queue compatibility window; no destructive down migration
or fallback authority is introduced here. Acceptance: DATA-02, REL-01, OBS-01.
