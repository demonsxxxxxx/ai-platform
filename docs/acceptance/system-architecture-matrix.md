# System Architecture Acceptance Matrix

Status: proposed cross-component acceptance criteria, not an execution report.
The detailed domain protocols and existing [SSE acceptance](../operations/redis-streams-sse-cutover-acceptance.md)
and [ordinary-user matrix](agent-app/ordinary-user-matrix.md) remain in force.
[Runtime convergence](../architecture/runtime-convergence.md) gives the proposed
migration direction. Human owners, exact source/image/config subjects and results
belong in the delivery issue/PR, using existing evidence levels.

## How to use

Select only scenarios reached by a slice, plus its owning regressions. A test name
or test file for a new scenario is a planned implementation until committed and
executed; this matrix does not claim such tests exist. Use the existing local
[test runner procedure](../agent-rules/local-test-execution.md) with real explicit
selectors. Required integration evidence uses real services and zero skips.
Do not invent CLI flags, skip a missing service, or infer a full pass from a
partially completed test run.

The evidence record includes acceptance ID, owner, exact source, image/config when
applicable, fixture/clock, fault location, command, observable state/identity and
negative assertions, counts, deadlines, result and cleanup. `not_run`, `blocked`,
`failed` and `passed` are distinct. Author text is not independent approval.

Correctness assertions below are hard gates. Performance targets must be declared
before measurement, use a named workload/profile and sufficient samples, and
separate queue, persistence, publication, receipt, application and paint latency.
Source defaults are not a proven capacity or latency SLO. A docs-only change runs
DOC-01; it does not need to pretend every runtime scenario was executed.

## Scenarios


### DOC-01 | Architecture

Boundary: `docs/README.md and detailed contracts`.

**Given:** The fixed baseline, the candidate documentation patch, and all local linked targets.

**When:** Build the full document disposition list; run exact-anchor, internal-link, requirement-ID and dependency checks.

**Required result:** Every core document has one disposition; active v4 and callback v2.1 are distinct; proposed work is not marked implemented; moved unique rules retain a detailed owner.

**Reject:** Missing links, a silent security/retention change, a passed label without execution evidence, or historical prose presented as deployed facts.

Evidence: Document validation, plus human content review. Budget: Complete inventory; zero unresolved changed local links and duplicate acceptance IDs.


### AUTH-01 | Identity

Boundary: `ADR 0007; authentication settings and session storage`.

**Given:** One newly authenticated browser session and a fixed authority clock.

**When:** Read, rotate a token, and update profile before and at the absolute expiry.

**Required result:** The token, server context and company-authority day retain one 86400-second absolute lifetime; activity never extends it; equality at expiry rejects; writes are not automatically replayed.

**Reject:** Reintroducing a 15-minute browser authority cutoff or claiming stream-lease expiry instantly revokes upstream company snapshots.

Evidence: Unit/contract, real session-store integration, deployed browser. Budget: Exactly the existing ADR lifetime; no new auth policy.


### AUTH-02 | Identity / Runs / Streaming

Boundary: `Worker reauthorization and stream lease owner`.

**Given:** A valid admitted scope plus an explicitly revoked platform authority and an already issued stream lease.

**When:** Attempt a new dispatch and renew/write frames across the lease deadline.

**Required result:** Current revoked platform authority denies new dispatch/renewal; frame admission stops at its allowed lease deadline; data already handed downstream is reported separately.

**Reject:** Unscoped authorization fallback or claiming browser-byte disappearance at revocation commit.

Evidence: Real database/Redis integration and controlled gateway. Budget: Existing at-most-15-second stream lease; does not shorten ADR 0007 upstream freshness.


### RUN-01 | Runs

Boundary: `app/routes/chat.py submission ledger`.

**Given:** A request with a stable submission ID and two concurrent callers; inject response loss after commit.

**When:** Retry the same request; query resolution; retry a conflicting payload with the same ID.

**Required result:** One admitted Run and one logical queue admission; exact retry returns the existing outcome; conflicting input is rejected; pending enqueue is distinguishable from proven rejection.

**Reject:** Creating a second Run, treating unknown commit as rejected-before-persist, or executing on client retry without identity.

Evidence: Real PostgreSQL/Redis integration. Budget: Bounded resolution and retry under the existing submission contract.


### RUN-02 | Runs / Streaming

Boundary: `Run lifecycle, attempt transition and terminal publication`.

**Given:** One running attempt; race success, cancellation and failure, including rollback before commit.

**When:** Inject transaction rollback and Redis failure immediately after a committed terminal.

**Required result:** One permitted terminal transition and exact durable facts; rollback exposes no committed terminal/end; post-commit outage retains pending publication with the same IDs/bytes.

**Reject:** A second terminal outcome or success inferred from SDK/Redis alone.

Evidence: Real PostgreSQL/Redis concurrency integration. Budget: Retries reuse frozen identity; measure pending age separately.


### RUN-03 | Runs / Execution / Sandbox

Boundary: `RunAttempt CAS and callback/reclaim boundaries`.

**Given:** An old dispatcher, a lawful asynchronous handoff, and a genuinely expired/revoked execution authority.

**When:** Return old heartbeats, callbacks, collection and terminal results after replacement.

**Required result:** Stale state mutations cannot affect the new owner; a still-authorized Executor callback during ordinary handoff remains valid; operation-specific fences are stated explicitly.

**Reject:** One universal generation that revokes legal handoffs, or queue metadata minting a new execution authorization.

Evidence: Real PostgreSQL/Redis integration; controlled provider for effects. Budget: No side effect from an expired authorization; provider effects require their own receipt/fence proof.


### IO-01 | Files / API

Boundary: `app/routes/files.py and app/storage.py`.

**Given:** Controlled synchronous object I/O blocks until released; parallel callback, SSE and lightweight request; a saturated offload pool.

**When:** Run part upload and completion/validation while the I/O stub stays blocked.

**Required result:** The event loop advances before I/O release; lightweight operations finish under their configured budget; queue/concurrency/byte limits hold; cancellation does not free an actually busy slot prematurely.

**Reject:** Direct blocking boto3 in async request work or unbounded detached threads.

Evidence: Deterministic scheduling test, real object-store integration, load test. Budget: Publish offload limits and measured callback/SSE latency; do not invent a p99 SLO.


### IO-02 | Files / Object Lifecycle

Boundary: `Multipart completion and metadata commit`.

**Given:** Object upload succeeds; metadata commit response is lost, or validation/metadata definitely fails.

**When:** Retry completion and run the authorized compensator.

**Required result:** Unknown commit is reconciled before delete; a committed file keeps its bytes; confirmed failure enters recoverable exact-target cleanup; retries do not create a second file.

**Reject:** Deleting bytes solely because the database response was lost or silently leaving untracked orphans.

Evidence: Real PostgreSQL/object-store fault integration. Budget: Object byte and parse limits remain unchanged; cleanup age is observable.


### TX-01 | Sandbox

Boundary: `Runtime heartbeat/renew and provider release`.

**Given:** Block the provider while another connection tries to lock the same business row.

**When:** Renew/stop using the proposed claim-I/O-receipt flow; expire the claim and return an old receipt.

**Required result:** Provider I/O holds no business-row transaction lock; the exact intent survives; stale receipts cannot finalize a new claim; unknown outcomes remain recoverable.

**Reject:** Simply unlocking old stop logic without a replacement concurrency protocol.

Evidence: Real PostgreSQL two-connection test and controlled provider. Budget: Provider call and total operation budgets declared before the run.


### TX-02 | Context / Sandbox

Boundary: `Executor context retrieval callback`.

**Given:** An exact snapshot-authorized object, slow byte read, and cancellation or scope change during I/O.

**When:** Read bytes outside the long transaction and recheck the applicable fence before response admission.

**Required result:** A competing Run transaction can proceed; approved bytes remain bounded; cancelled or foreign authority cannot receive a newly admitted response.

**Reject:** Authorizing only before I/O and returning after authority loss without the defined final check.

Evidence: Real PostgreSQL/object-store integration. Budget: Object limit and total request deadline explicitly recorded.


### SBX-01 | Sandbox

Boundary: `Runtime acquire/stage/validate and creation claim`.

**Given:** Provider creation succeeds with response loss; process exits before lease confirmation; wrong-image/scope handle is returned.

**When:** Recover the exact attempt without blindly creating another resource.

**Required result:** Verified exact resource is adopted or compensated under an owned claim; unprovable handles are quarantined; SDK dispatch occurs only after authorized readiness.

**Reject:** Resource reuse based only on a display name or an SDK session ID.

Evidence: Controlled real provider and database fault integration. Budget: Existing resource/start/cleanup bounds; unknown state is not success.


### SBX-02 | Sandbox / Runs

Boundary: `Provider stop and lease release/reconcile`.

**Given:** Run is terminal; provider stop fails or succeeds but its durable receipt is uncertain.

**When:** Retry/reconcile, then deliver an obsolete operation result.

**Required result:** Business terminal stays truthful; cleanup remains pending until proven; stale result cannot release a replacement resource; no later work is authorized on a releasing resource.

**Reject:** Marking released before proven stop or rerunning a successful task to repair cleanup.

Evidence: Real provider/database integration. Budget: Exact operation identity and cleanup deadline; total shutdown is bounded.


### RCV-01 | Execution

Boundary: `app/executor_reconciler.py`.

**Given:** At least three eligible terminal receipts and no notifications after they are inserted.

**When:** Run reconciliation with the configured count/time budget.

**Required result:** Eligible backlog continues within bounded drain passes without an unconditional idle wait between each receipt; other scheduled work can still run.

**Reject:** Processing one receipt and sleeping solely because no new notification arrived.

Evidence: Deterministic scheduling plus real database integration. Budget: Drain count/time budget is explicit; no historical timing promoted to SLO.


### RCV-02 | Execution

Boundary: `app/runtime/sandbox/executor_signals.py`.

**Given:** A signal arrives after the empty scan but before waiting; another signal is lost entirely.

**When:** Cross the scan-to-wait boundary and run the fallback scan.

**Required result:** The raced signal is observed by a retained cursor/handshake, or durable work is found within the declared fallback bound; notifications cannot delete work.

**Reject:** Repeated use of a fresh-only cursor as a guarantee of lossless wake-up.

Evidence: Real Redis/database integration. Budget: Measure maximum detection gap against the declared fallback interval.


### RCV-03 | Execution / Object Lifecycle

Boundary: `Worker maintenance phase scheduler`.

**Given:** A bulk cleanup never completes within its deadline; critical publication and reclaim work is eligible.

**When:** Run the proposed isolated schedules and cancel the slow phase.

**Required result:** Critical work advances; the slow phase has a finite retry time and visible failure; no unbounded replacement tasks accumulate; held resources remain accounted for.

**Reject:** One serial await blocking all recovery or suppressing a hung task without tracking it.

Evidence: Scheduling test and dependency fault integration. Budget: Independent phase deadlines and resource caps, not exception handling alone.


### RCV-04 | Execution

Boundary: `Worker supervisor for count 1 and N`.

**Given:** Run the same workload with one and multiple slots; inject supervisor-child failure and shutdown.

**When:** Start, dispatch, cancel, and close the process.

**Required result:** Both counts use the same maintenance/reconciler lifecycle; no bulk-cleanup startup prerequisite; child failure is surfaced; all owned tasks/clients close or have explicit unknown outcome.

**Reject:** Different control semantics at count 1 or a live heartbeat concealing dead critical work.

Evidence: Process integration and packaged-image stop test. Budget: One absolute shutdown budget per process, including nested retries.


### CB-01 | Sandbox / Engine

Boundary: `Message delta callback delivery`.

**Given:** Projected adjacent deltas, different message identity, multibyte text, Tool barrier and terminal; inject lost HTTP response.

**When:** Batch and retry while preserving single sender order.

**Required result:** Original event identities and exact retry bytes remain; barriers do not overtake; local admission differs from durable receipt; first-public-text metric is recorded on V4 without a legacy duplicate.

**Reject:** Renumbering/recombining durable events or treating queue admission as Tool approval.

Evidence: Focused callback/SDK contract tests. Budget: Existing 100-event and 8 KiB aggregate-text bounds; serialized bytes measured separately.


### CB-02 | Sandbox

Boundary: `Callback batch clock, cancellation and shutdown`.

**Given:** An aged queued delta, a barrier during optional batching delay, an in-flight retry and repeated cancellation.

**When:** Flush and close against one monotonic deadline.

**Required result:** Aged work gets no fresh full batching sleep; barrier ends optional wait; in-flight order remains; deadline stops new work and reports unconfirmed outcomes honestly.

**Reject:** Indefinite shield/gather or a fabricated positive receipt to force shutdown.

Evidence: Deterministic clock/scheduling and process stop tests. Budget: 50 ms denotes intended batching delay, not network or total queue-latency proof.


### CB-03 | Sandbox / Streaming

Boundary: `Callback persistence and durable publisher`.

**Given:** Exact callback batch commits, Redis publication blocks/fails, notification is lost.

**When:** Use the proposed durability-acknowledgement fast path and restart publication.

**Required result:** Acknowledgement matches the committed batch only; durable indexed work survives and publishes in order; no request-owned background task is the sole recovery mechanism.

**Reject:** Acknowledging before commit or changing Tool receipt semantics without a protocol-owner decision.

Evidence: Real PostgreSQL/Redis fault integration. Budget: Response/publication budgets measured independently; activate only after contract approval.


### SSE-01 | Streaming / Frontend

Boundary: `Accepted semantic sequence and transport cursor`.

**Given:** History contains seq 10; a new seq 11 has the same parsed timestamp; later semantic duplicate has a newer transport ID.

**When:** Apply, replay and reconnect.

**Required result:** Seq 11 text applies once despite timestamp equality; a validated semantic duplicate advances only allowed transport progress; cursor never passes unapplied required state.

**Reject:** Wall-clock ordering or reconnection based on an uncommitted render.

Evidence: Frontend contract and mounted component test. Budget: Zero lost/duplicate application under the fixed corpus.


### SSE-02 | Frontend

Boundary: `Pure reducer and state store`.

**Given:** Deferred React rendering and no animation-frame callback; replay the updater with the same previous state.

**When:** Apply text, Tool progress and terminal preparation.

**Required result:** Pure computation is repeatable; one committed store snapshot contains both text and accepted progress; rendering delay does not own acceptance.

**Reject:** Mutating external refs/cursors inside React updater computations.

Evidence: Reducer unit and mounted browser tests. Budget: Bound retained state; test background tab behavior separately.


### SSE-03 | Streaming / Frontend

Boundary: `Terminal validation and deferred hydration`.

**Given:** A valid terminal is hydrating; forged/foreign/malformed end has a matching terminal ID.

**When:** Feed the invalid frame and then complete hydration.

**Required result:** Invalid frame remains rejected and never enters deferred acceptance or cursor commit; only fully validated matching terminal/end can settle.

**Reject:** A boolean false that conflates invalid and valid-but-deferred results.

Evidence: Adapter/connection contract test. Budget: Zero invalid cursor advancement.


### SSE-04 | Streaming / Frontend

Boundary: `Hydration snapshot/stream anchor`.

**Given:** Concurrent durable writes during active hydration; retained gaps and a missing current stream.

**When:** Install the snapshot and resume from a server-proven anchor.

**Required result:** Snapshot coverage and cursor form one verified consistent cut; later events apply once; no invented cursor, same-incarnation recreation, or unapproved active successor.

**Reject:** Pairing unrelated latest-history and latest-Redis reads.

Evidence: Real PostgreSQL/Redis plus frontend integration. Budget: Existing terminal-only successor boundary preserved.


### SSE-05 | Streaming

Boundary: `Shared Pub/Sub reader/control acknowledgements`.

**Given:** A malformed publication on channel A arrives before the subscription acknowledgement for B; C is healthy.

**When:** Invalidate A while B subscribes and C receives data.

**Required result:** Reader keeps consuming acknowledgements; B completes and C remains live; A failure is isolated unless transport itself is truly lost.

**Reject:** Waiting on a control lock that is held by code waiting for that same reader.

Evidence: Deterministic race and real Redis integration. Budget: Control deadline and per-browser count/byte caps retained.


### SSE-06 | Streaming / Frontend

Boundary: `Schema, generated types and runtime validators`.

**Given:** Valid/invalid payload corpus covering every event/control family and cross-field bindings.

**When:** Generate and compare structural validators; run semantic checks and historical normalization.

**Required result:** One schema owns structural rules; strict semantic owner/cursor checks remain; V4 live and normalized history feed the same state semantics.

**Reject:** Types-only parity presented as runtime validation or hand-written divergent field registries.

Evidence: Generator parity, contract corpus and frontend tests. Budget: All current event/control families covered; no wire expansion in a source-only move.


### OWN-01 | Runs / Conversations / Agent Apps

Boundary: `Submission and cancellation use cases`.

**Given:** Existing success, denial, response-loss and concurrent-admission corpus through old routes and new owner.

**When:** Extract the complete use case without changing behavior.

**Required result:** Same responses, identity, transaction/lock order, effects and errors; route is translation only; old independent implementation is removed.

**Reject:** A generic platform service, independent nested commits, or a duplicate writable facade.

Evidence: Contract replay, real PostgreSQL concurrency and architecture tests. Budget: Smallest complete caller closure; no arbitrary line-count target.


### OWN-02 | Sandbox / Execution

Boundary: `Sandbox public port and Engine adapter`.

**Given:** At least one real provider contour plus test-only doubles and private SDK event fixtures.

**When:** Route acquire/renew/collect/release through the owner and replace Engine fixtures.

**Required result:** No route/peer constructs a provider or handles SDK internals; fake capability remains test-only; exact public contracts stay stable.

**Reject:** Moving files while retaining the bypass path or creating a second lifecycle ledger.

Evidence: Architecture, provider/Engine contract and packaged integration. Budget: Remove the inventoried obsolete path in the same bounded migration.


### CTX-01 | Context / Agent Apps

Boundary: `Conversation materialization and pinned Agent definition`.

**Given:** A prior answer longer than 640 characters defines A/B; next user sends A; include foreign, missing and historical-system messages.

**When:** Prepare executor context and retry from the same snapshot.

**Required result:** Exact latest complete turn is present without model-initiated retrieval; current request occurs once; foreign/missing selection fails; historical instructions/tools grant no authority.

**Reject:** Public summary used as executor history or current capability inferred from old tool calls.

Evidence: Context/Engine contract plus real storage integration. Budget: Existing total-context and field limits; hard overflow is explicit.


### DATA-01 | Files / Artifacts / Object Lifecycle

Boundary: `Object deletion outbox and bucket/client lifecycle`.

**Given:** Exact authorized unbound file, referenced file, artifact and stale deletion generation; restricted storage principal.

**When:** Claim, delete, lose receipt and retry; try a stale completion.

**Required result:** Only eligible exact target is deleted; target and receipt commit together; stale generation rejects; runtime I/O needs no bucket-admin permission; clients close.

**Reject:** Broad key deletion, bucket provisioning per put, or treating a failed receipt as success.

Evidence: Real PostgreSQL/object-store and least-privilege integration. Budget: Existing target namespaces and immutable reference protections remain.


### DATA-02 | Data owners

Boundary: `Retention and compatibility contracts`.

**Given:** Retain-by-default settings, unsupported nonzero retention, historical rows and legacy config consumers.

**When:** Validate settings, plan age-based cleanup, and attempt early alias removal.

**Required result:** Unsupported retention cannot delete data; approved new cleanup respects all references and replay/receipt windows; alias retirement requires consumer evidence and its published earliest date.

**Reject:** Deleting history for code neatness or treating 2026-11-01 alone as sufficient retirement proof.

Evidence: Settings/contract plus real reference-integrity tests for later cleanup. Budget: Existing 2026-10-31 support commitment retained; no destructive action in docs maintenance.


### REL-01 | Release / Security

Boundary: `Effective Compose, image and migration authority`.

**Given:** Exact code/image/config subjects, governed and internal-test profiles, predecessor schema and rollback candidate.

**When:** Inspect merged configuration; run approved package/deploy/rollback acceptance.

**Required result:** Production dependency ports and credentials match governed topology; no fake provider or test credential exception; migration and rollback preserve facts; evidence binds exact subjects.

**Reject:** Inferring exposure from base Compose alone or counting CI as deployed acceptance.

Evidence: Required CI, image inspection and controlled deployed acceptance. Budget: Existing runbook and rollback guards; no release permission from this document.


### OBS-01 | Execution / Platform

Boundary: `Progress metrics and capacity profile`.

**Given:** Known eligible backlog, constrained storage/DB resources and multiple active run stages.

**When:** Measure progress and resource usage under a predeclared fixture/profile.

**Required result:** Heartbeat, last successful progress and oldest pending age are distinct; stage concurrency/bytes remain bounded; labels contain no content or high-cardinality identities; capacity claim names its profile.

**Reject:** Deriving throughput from worker_count or claiming p99 from a tiny sample.

Evidence: Deterministic metrics test and controlled load measurement. Budget: Record duration, sample count, event rates, artifact sizes and percentiles; unmet/unmeasured SLO remains pending.
