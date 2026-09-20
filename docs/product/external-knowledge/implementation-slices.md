# Atomic Implementation Change Contracts

## 1. Delivery rule

Each slice is one independently reviewable Issue and one pull request. A slice
may be split again when its exact-base Issue discovers another transaction
owner, public contract or rollback boundary; it may not absorb a neighboring
slice. GitHub owns assignee, branch, current status, exact base/head, review,
checks and merge state.

Every Issue records the exact base, path lease, exclusively owned atomic
requirements and cases from `traceability-matrix.md`, affected requirements,
owning tests, rollback, stop condition and evidence ceiling. Every pull request
proves only the evidence layer named here. Source, CI/build, packaged image,
deployment, runtime and external acceptance remain separate claims.

## 2. Global invariants

1. One write authority owns each business fact.
2. HTTP routes, Agent Apps, Runs, Streaming and Engine adapters never call a
   provider directly.
3. Credential bytes remain inside shared secret infrastructure and the admitted
   Knowledge provider call.
4. Browser or model input cannot choose provider URL, dataset identity, ACL or
   credential.
5. Global route and repository modules gain no RAGFlow conditionals.
6. SSE v4 remains the public terminal/hydrate authority.
7. Schema changes are additive and rolling-reader compatible.
8. Every changed behavior has one focused falsifiable owning check.
9. Cleanup stops an old writer before removing a compatibility reader.
10. Provider and database payload bounds are enforced before persistence or
    Engine dispatch.

## 3. Dependency index

| Slice | Atomic outcome | Depends on | Evidence ceiling |
| --- | --- | --- | --- |
| KDOC-00 | Durable PRD, traceability and baseline inventory | none | Document/source review |
| KTRACE-62 | Required atomic-case manifest and validator | KDOC-00 | CI/source governance |
| KADR-01 | Accepted Knowledge authority and secret-boundary decision | KTRACE-62 | Architecture review |
| KINV-02 | Exact-base persisted and external consumer inventory | KADR-01 | Read-only inventory |
| KDOM-03 | Provider-neutral vocabulary, errors and ports | KADR-01 | Unit/architecture |
| KSTATE-04 | Pure lifecycle transition functions | KDOM-03 | Unit |
| KACLDM-05 | Canonical source ACL evaluator and containment | KDOM-03 | Unit/property |
| KPROFDM-06 | Retrieval-profile values, defaults and bounds | KDOM-03 | Unit |
| KNORM-07 | Pure provider-response normalization | KDOM-03 | Unit/fuzz |
| KRANK-08 | Pure deduplication and deterministic RRF | KDOM-03 | Unit/property |
| KDBCON-09 | Connection and immutable revision persistence | KSTATE-04 | Migration/PostgreSQL |
| KDBSYNC-10 | Synchronization job, lease and candidate persistence | KDBCON-09, KSTATE-04 | Migration/PostgreSQL |
| KDBSRC-11 | Logical source persistence | KDBCON-09, KSTATE-04 | Migration/PostgreSQL |
| KDBACL-12 | Immutable source ACL-version persistence | KDBSRC-11, KACLDM-05 | Migration/PostgreSQL |
| KDBAGT-13 | Agent revision Knowledge binding persistence | KDBSRC-11, KPROFDM-06 | Migration/PostgreSQL |
| KDBRUN-14 | Run Knowledge Snapshot persistence | KDBAGT-13 | Migration/PostgreSQL |
| KDBATT-15 | Retrieval-attempt persistence and generation fence | KDBRUN-14, KSTATE-04 | Migration/PostgreSQL |
| KDBEVD-16 | Durable bounded evidence persistence | KDBATT-15, KNORM-07 | Migration/PostgreSQL |
| KDBCIT-17 | Citation schema and message-binding transaction primitive | KDBEVD-16 | Migration/PostgreSQL |
| KPRVCAT-18 | RAGFlow authenticated check and catalog adapter | KDOM-03 | Provider contract |
| KPRVRET-19 | RAGFlow native retrieval adapter | KNORM-07, KPROFDM-06 | Provider contract |
| KCON-20 | Authorized connection application/API lifecycle | KDBCON-09, KPRVCAT-18 | Route/integration |
| KSYNC-21 | Idempotent complete catalog synchronization | KDBSYNC-10, KDBSRC-11, KPRVCAT-18 | Concurrency/integration |
| KSOURCE-22 | Authorized source presentation and lifecycle API | KDBSRC-11, KSTATE-04 | Route/integration |
| KACL-23 | Authorized source ACL replacement API | KDBACL-12, KACLDM-05 | Authorization/integration |
| KADMIN-24 | Knowledge Connections frontend | KCON-20 | Component/browser/build |
| KSRCUI-25 | Knowledge Sources catalog and ACL frontend | KSYNC-21, KSOURCE-22, KACL-23 | Component/browser/build |
| KSRCTEST-26 | Ephemeral administrator source-test operation | KPRVRET-19, KSOURCE-22 | Route/provider/security |
| KSRCTESTUI-27 | Source-test frontend states | KSRCTEST-26, KSRCUI-25 | Component/browser/build |
| KPROF-28 | Agent Apps private/public Knowledge DTOs | KDBAGT-13, KACL-23 | Contract/regression |
| KBUILD-29 | Builder multi-source and profile editor | KPROF-28 | Component/browser/build |
| KBLDTEST-30 | Builder Test Conversation Knowledge admission | KPROF-28, KDBRUN-14 | Run/integration |
| KBLDTESTUI-31 | Builder test controls and result isolation UX | KBLDTEST-30, KBUILD-29 | Component/browser |
| KPUB-32 | Publication source/ACL/profile validation | KPROF-28, KACL-23 | Authorization/concurrency |
| KMARKET-33 | Market discovery/detail source authorization | KPUB-32 | Authorization/component |
| KADM-34 | Per-Run source and connection reauthorization | KPUB-32, KACL-23 | Authorization/concurrency |
| KSNAP-35 | Atomic Run Knowledge Snapshot admission | KADM-34, KDBRUN-14 | PostgreSQL/Run |
| KREXEC-36 | Generation claim, provider fan-out and cancellation | KSNAP-35, KDBATT-15, KPRVRET-19 | Execution/concurrency |
| KNORMAPP-37 | Typed response validation and safe error mapping | KREXEC-36, KNORM-07 | Execution/provider |
| KFUSE-38 | Deduplication, RRF and evidence commit | KNORMAPP-37, KRANK-08, KDBEVD-16 | Unit/PostgreSQL |
| KOUTCOME-39 | Required-source failure and no-evidence terminal paths | KFUSE-38 | Run/terminal |
| KENG-40 | Claude adapter evidence rendering | KFUSE-38 | Adapter/security |
| KCIT-41 | Same-Run citation finalization orchestration | KENG-40, KDBCIT-17 | Atomicity/concurrency |
| KHYDRATE-42 | Authorized history and terminal-hydrate citations | KCIT-41 | Route/stream/reducer |
| KWORKUI-43 | Workspace inline citation and detail UX | KHYDRATE-42 | Component/browser/a11y |
| KLOG-44 | Query, content, credential and body log redaction | KPRVCAT-18, KPRVRET-19 | Security/fault injection |
| KMETRIC-45 | Safe provider/retrieval metrics | KREXEC-36 | Metrics/fault injection |
| KREADY-46 | Registry, configuration and optional-provider readiness | KCON-20 | Readiness/startup |
| KRETRY-47 | Provider permits, retry and deadline policy | KREXEC-36 | Concurrency/fault injection |
| KALERT-48 | Operator alerts, recovery and audit | KSYNC-21, KREADY-46, KMETRIC-45, KRETRY-47 | Operations/fault injection |
| KLIFE-49 | Retention, reference protection and source-test cleanup | KDBSYNC-10, KDBEVD-16, KDBCIT-17 | Lifecycle/PostgreSQL |
| KCLEANSEED-50 | Stop seeded RAGFlow Skill/version/distribution writer | KINV-02, KPROF-28 | Migration/absence |
| KCLEANMCPSEED-60 | Stop seeded RAGFlow MCP tool/policy writer | KINV-02, KPRVRET-19 | Migration/absence |
| KCLEANAGTSEED-61 | Stop seeded SOP Agent writer | KINV-02, KPUB-32, KMARKET-33 | Migration/absence |
| KCLEANSKILL-51 | Retire active seeded Skill selection | KCLEANSEED-50, KPROF-28 | Consumer/replay proof |
| KCLEANMCP-52 | Retire RAGFlow MCP built-in special cases | KCLEANMCPSEED-60, KPRVRET-19 | MCP/regression |
| KCLEANROUTE-53 | Retire hard-coded capability/intent/Chat writers | KCLEANAGTSEED-61, KMARKET-33, KADM-34 | Route/regression |
| KCOMPAT-54 | Apply historical alias/redaction/error reader dispositions | KINV-02, KHYDRATE-42 | Reader/writer matrix |
| KCLEANDEPLOY-55 | Remove retired deploy text and update readiness/test ownership | KCLEANSEED-50, KCLEANMCPSEED-60, KCLEANAGTSEED-61, KCOMPAT-54 | Packaging/readiness |
| KCI-56 | Required changed-path and packaged-image gates | all pre-runtime required slices | CI/build/image |
| KCONCUR-57 | Fifty-Run platform-controlled capacity acceptance | KRETRY-47, KCIT-41, KMETRIC-45 | Controlled capacity |
| KACCEPT-58 | Exact-subject company RAGFlow runtime acceptance | KCI-56, KCONCUR-57 | Runtime/external |
| KAGENTIC-59 | Agent-directed generic Knowledge capability | KACCEPT-58 | Separate release |

For KCI-56, all pre-runtime required slices means the exact machine-readable set
below: all 63 slices except documentation-only KDOC-00, the KCI-56 aggregator
itself, controlled-capacity KCONCUR-57, external runtime KACCEPT-58 and follow-on
KAGENTIC-59. It includes required inventory KINV-02 and the independently gated
MCP and Agent seed-writer slices. KCI-56 validates its own KOPS-018 and KOPS-019
manifest in addition to this upstream set. KACCEPT-58 owns KOPS-020 and cannot
waive a failed or skipped upstream member.

The machine-readable required set for KCI-56 is:

```text
KADR-01
KTRACE-62
KINV-02
KDOM-03
KSTATE-04
KACLDM-05
KPROFDM-06
KNORM-07
KRANK-08
KDBCON-09
KDBSYNC-10
KDBSRC-11
KDBACL-12
KDBAGT-13
KDBRUN-14
KDBATT-15
KDBEVD-16
KDBCIT-17
KPRVCAT-18
KPRVRET-19
KCON-20
KSYNC-21
KSOURCE-22
KACL-23
KADMIN-24
KSRCUI-25
KSRCTEST-26
KSRCTESTUI-27
KPROF-28
KBUILD-29
KBLDTEST-30
KBLDTESTUI-31
KPUB-32
KMARKET-33
KADM-34
KSNAP-35
KREXEC-36
KNORMAPP-37
KFUSE-38
KOUTCOME-39
KENG-40
KCIT-41
KHYDRATE-42
KWORKUI-43
KLOG-44
KMETRIC-45
KREADY-46
KRETRY-47
KALERT-48
KLIFE-49
KCLEANSEED-50
KCLEANSKILL-51
KCLEANMCP-52
KCLEANROUTE-53
KCOMPAT-54
KCLEANDEPLOY-55
KCLEANMCPSEED-60
KCLEANAGTSEED-61
```

## 4. Detailed slice contracts

The `Requirements` field below is a non-owning impact list: it names behavior
that the slice defines, supports or constrains. Exclusive atomic requirement and
test-manifest ownership comes only from `traceability-matrix.md`. An Issue copies
its owned IDs from that matrix and records any additional impacted IDs
separately, so overlapping prerequisites do not create a second acceptance
owner.

### KDOC-00 — Product requirement authority

- **Owns:** docs/product and the product entry in docs/README.md.
- **Requirements:** all requirements as documentation authority.
- **Acceptance:** unique IDs, valid links, complete traceability, no delivery
  status claims, and independent product/architecture/atomicity review.
- **Rollback:** revert documentation only.
- **Stop:** an unresolved company-specific value would need to be invented.

### KTRACE-62 — Atomic-case manifest gate

- **Owns:** one versioned machine-readable slice-manifest schema, validator,
  focused tests and required changed-path check for External Knowledge slices.
- **Requirements:** KOPS-035.
- **Acceptance:** the validator derives the exact owned case set from the
  traceability matrix and rejects missing, extra, duplicate, unknown and
  differently owned IDs. The bootstrap pull request includes its own valid
  manifest fixture; the required check becomes active for KADR-01 and every
  later slice.
- **Rollback:** dependent Knowledge slices remain blocked until an equivalent
  required validator is restored.
- **Stop:** the check can be bypassed for a Knowledge path or cannot bind the
  candidate to an exact slice identity.

### KADR-01 — Knowledge authority decision

- **Owns:** one ADR, runtime/source authority maps, CONTEXT.md and architecture
  policy inputs.
- **Requirements:** KMIG-001..KMIG-005, KMIG-015.
- **Acceptance:** Knowledge is the only product authority; MCP stays generic;
  Engine-model and Knowledge-provider credential boundaries are distinct; one
  transaction owner is named for Run snapshot and citation finalization; the
  single-enterprise identity boundary remains non-configurable and non-public.
- **Rollback:** revert the decision before dependent code merges.
- **Stop:** reviewers cannot agree on cross-context transaction ownership.

### KINV-02 — Exact-base consumer inventory

- **Owns:** the Issue record derived from baseline-disposition.md; no product
  behavior.
- **Requirements:** KMIG-006..KMIG-009, KMIG-013..KMIG-014.
- **Acceptance:** exact persisted counts, queued/retryable subjects, external
  aliases, route consumers, readiness selectors and per-item reader/writer
  disposition.
- **Rollback:** none.
- **Stop:** a persisted identity or external consumer remains unobservable.

### KDOM-03 — Vocabulary, errors and provider ports

- **Owns:** pure Knowledge value records, provider-neutral errors and port
  protocols.
- **Requirements:** KRET-003..KRET-006, KMIG-011..KMIG-012.
- **Acceptance:** no FastAPI, transport DTO, PostgreSQL, Redis, SDK or provider
  record imports; architecture tests enforce dependency direction.
- **Rollback:** remove the unconsumed package.
- **Stop:** an application contract requires a RAGFlow-specific type.

### KSTATE-04 — Lifecycle transitions

- **Owns:** pure Connection, Source, Sync and Retrieval Attempt transition
  functions.
- **Requirements:** KCON-011..KCON-012, KSRC-010, KSRC-020..KSRC-024,
  KOPS-021.
- **Acceptance:** every declared transition passes; every undeclared
  transition, stale generation and post-terminal mutation fails.
- **Rollback:** remove pure transitions before persistence consumes them.
- **Stop:** a transition has no named command or authority.

### KACLDM-05 — Source authorization semantics

- **Owns:** pure source ACL evaluator, scope containment and validated authority
  sets.
- **Requirements:** KACL-001..KACL-016, KAGT-019.
- **Acceptance:** property tests match canonical Agent ACL department, role and
  user semantics, including fail-closed identity outage.
- **Rollback:** source activation/publication remains disabled.
- **Stop:** Source and Agent ACL hierarchy semantics diverge.

### KPROFDM-06 — Retrieval-profile contract

- **Owns:** immutable profile values, defaults, numeric and byte bounds.
- **Requirements:** KAGT-013, KAGT-018, KAGT-022,
  KRET-009..KRET-015, KRET-046.
- **Acceptance:** every minimum, maximum, default and invalid cross-field
  combination has a focused check.
- **Rollback:** reject Knowledge bindings.
- **Stop:** a provider-only option leaks into the product profile.

### KNORM-07 — Provider normalization

- **Owns:** pure typed provider-response validation and bounded projection.
- **Requirements:** KRET-016..KRET-023, KRET-030..KRET-035.
- **Acceptance:** independent missing/invalid/oversize/unknown-field and
  dataset-mismatch tests plus fuzzed payload bounds.
- **Rollback:** provider adapter remains unregistered.
- **Stop:** the deployed provider cannot supply stable document identity.

### KRANK-08 — Deduplication and RRF

- **Owns:** pure composite identity, deterministic RRF and final bound
  selection.
- **Requirements:** KRET-024..KRET-029.
- **Acceptance:** permutation/property tests prove stable ties, duplicate
  elimination, count cap and byte cap.
- **Rollback:** Knowledge Runs remain denied.
- **Stop:** ranking requires provider-private mutable state.

### KDBCON-09 — Connection persistence

- **Owns:** connection/revision migration, repository and indexes.
- **Requirements:** KCON-009..KCON-011, KCON-017..KCON-023, KCON-035,
  KLIFE-011..KLIFE-013.
- **Acceptance:** additive idempotent migration, immutable revisions and
  lifecycle receipts, secret reference only, unique name/revision, one fenced
  candidate pointer, monotonic lifecycle epoch, atomic active revision/catalog
  references, pre-authority epoch-zero behavior and bounded fields.
- **Rollback:** older application ignores additive tables; rows remain.
- **Stop:** credential bytes would enter PostgreSQL.

### KDBSYNC-10 — Synchronization persistence

- **Owns:** sync job, monotonic lease, candidate observation and receipt
  persistence.
- **Requirements:** KSRC-003..KSRC-005, KSRC-017..KSRC-020, KSRC-042,
  KOPS-021, KLIFE-007.
- **Acceptance:** duplicate operation identity, active-vs-candidate purpose,
  exact connection revision, stale lease, crash before commit, unknown commit
  outcome, candidate cleanup and source-then-connection writer lock-order tests.
- **Rollback:** stop claims; retain receipts and last complete catalog.
- **Stop:** a partial candidate can modify absence state.

### KDBSRC-11 — Logical source persistence

- **Owns:** source schema, private provider identity, presentation and indexes.
- **Requirements:** KSRC-008..KSRC-016, KSRC-021..KSRC-031.
- **Acceptance:** exact uniqueness, bounded metadata, alias preservation,
  private identity redaction and historical binding reads.
- **Rollback:** older application ignores additive rows.
- **Stop:** document, chunk or embedding payloads would enter the catalog.

### KDBACL-12 — Source ACL persistence

- **Owns:** immutable ACL root/child records and authorization version.
- **Requirements:** KACL-001..KACL-008, KACL-016..KACL-017, KLIFE-006.
- **Acceptance:** exact-set replacement creates one version, retains the prior
  version and rolls back atomically on any invalid authority.
- **Rollback:** sources remain pending review.
- **Stop:** ACL mutation would rewrite a referenced version.

### KDBAGT-13 — Agent revision bindings

- **Owns:** ordered source bindings and exact profile/version fields on
  immutable Agent revisions.
- **Requirements:** KAGT-003..KAGT-005, KAGT-014..KAGT-015,
  KAGT-020..KAGT-025.
- **Acceptance:** zero/eight/ninth/duplicate/order/hash and rolling-reader
  compatibility tests.
- **Rollback:** stop accepting new bindings; retain rows.
- **Stop:** a browser value becomes binding authority.

### KDBRUN-14 — Run Knowledge Snapshot

- **Owns:** additive snapshot schema, repository and Run foreign-key/index
  contract.
- **Requirements:** KADM-018..KADM-025.
- **Acceptance:** one snapshot per Run, exact versions and connection lifecycle
  epochs, content hash, payload bound, no secret/query/content and orphan-free
  rollback under concurrent lifecycle transition.
- **Rollback:** deny bound Runs; retain snapshots.
- **Stop:** Run and snapshot cannot be committed through one owner transaction.

### KDBATT-15 — Retrieval-attempt fence

- **Owns:** attempt schema, status receipt and generation-fenced repository.
- **Requirements:** KRET-001..KRET-002, KRET-041..KRET-043,
  KRET-047..KRET-048.
- **Acceptance:** same-generation idempotency; stale/post-terminal writes fail;
  the claim fixes `started_at` and `deadline_at`; cancellation-vs-deadline
  terminal compare-and-set race; safe counts and outcome only.
- **Rollback:** deny retrieval claims.
- **Stop:** worker identity cannot be fenced by Run attempt generation.

### KDBEVD-16 — Durable evidence

- **Owns:** evidence schema, immutable repository, byte/count checks and
  indexes.
- **Requirements:** KRET-021..KRET-022, KRET-028..KRET-035,
  KLIFE-001..KLIFE-004.
- **Acceptance:** evidence persists before Engine dispatch, survives worker
  recovery and Engine continuation, rejects stale Run/generation and never
  stores an unbounded provider response; success evidence and its retrieval
  terminal receipt commit atomically.
- **Rollback:** deny Engine dispatch for bound Runs; retain rows.
- **Stop:** evidence ownership is split across process memory and PostgreSQL.

### KDBCIT-17 — Citation persistence primitive

- **Owns:** citation schema, indexes and transaction primitive used by
  Conversations orchestration.
- **Requirements:** KCIT-002..KCIT-014, KLIFE-005.
- **Acceptance:** same-message uniqueness, field bounds, same-Run foreign keys,
  message/citation commit and rollback.
- **Rollback:** grounded success remains disabled; retain rows.
- **Stop:** message and citation cannot share atomic orchestration.

### KPRVCAT-18 — RAGFlow catalog adapter

- **Owns:** authenticated check, paginated dataset listing and catalog
  normalization behind the provider port.
- **Requirements:** KCON-012..KCON-016, KCON-026, KSRC-006..KSRC-007.
- **Acceptance:** Bearer authentication, redirect/egress/TLS/timeout,
  every-page traversal, typed envelope and raw-body redaction.
- **Rollback:** unregister the adapter.
- **Stop:** the company endpoint differs from the authenticated dataset-list
  contract.

### KPRVRET-19 — RAGFlow retrieval adapter

- **Owns:** one-source native /api/v1/retrieval request and typed result
  translation.
- **Requirements:** KRET-006..KRET-013, KRET-016..KRET-023, KRET-046.
- **Acceptance:** exact server-resolved dataset, bounded question, result page,
  candidate pool and threshold, response identity validation and safe error
  mapping.
- **Rollback:** unregister retrieval while catalog administration remains.
- **Stop:** stable document or promised chunk identity is unavailable.

### KCON-20 — Connection application and routes

- **Owns:** create/update/check/activate-candidate/disable/list/detail commands,
  authorization, idempotency and safe DTOs.
- **Requirements:** KCON-001..KCON-008, KCON-017..KCON-034,
  KCON-036..KCON-041, KSRC-041.
- **Acceptance:** every command role, denied-target non-disclosure, base/provider
  change rejection, idempotent server-resolved candidate check-sync-switch race,
  freshness/epoch binding, write-only secret and bounded pagination tests.
- **Rollback:** disable routes; retain connection rows and secret references.
- **Stop:** a read DTO can recover credential bytes.

### KSYNC-21 — Catalog synchronization application

- **Owns:** one idempotent, paginated, lease-fenced sync command and safe job
  projection.
- **Requirements:** KSRC-001..KSRC-025, KSRC-034, KSRC-042, KOPS-021,
  KOPS-029.
- **Acceptance:** complete commit, partial preservation, missing transition,
  retry linkage, lease expiry, concurrent request and source-then-connection
  lock-order tests.
- **Rollback:** stop new jobs; retain last complete catalog.
- **Stop:** provider pagination has no deterministic completion signal.

### KSOURCE-22 — Source lifecycle and presentation API

- **Owns:** list/detail, alias/description and activate/disable/re-enable
  commands.
- **Requirements:** KSRC-023..KSRC-031, KSRC-035..KSRC-036.
- **Acceptance:** management authorization, all state transitions, filters,
  search, pagination and private-ID redaction.
- **Rollback:** disable mutations and new activation; retain rows.
- **Stop:** re-enable can bypass ACL or active-connection checks.

### KACL-23 — Source ACL API

- **Owns:** authorized exact-set replacement, safe projection and audit.
- **Requirements:** KACL-003..KACL-022.
- **Acceptance:** canonical authority validation, concurrent version conflict,
  denial non-disclosure and no credential read.
- **Rollback:** disable ACL replacement and source activation.
- **Stop:** another route writes ACL child rows directly.

### KADMIN-24 — Connections frontend

- **Owns:** connection route, typed client and accessible form/list/detail
  components.
- **Requirements:** KUI-001..KUI-004, KUI-024..KUI-025.
- **Acceptance:** write-only credential, checking/success/failure states,
  pagination, keyboard flow and narrow viewport scroll.
- **Rollback:** remove navigation/route; backend remains safely unused.
- **Stop:** frontend requires raw provider responses.

### KSRCUI-25 — Sources frontend

- **Owns:** source catalog, filters, sync receipt, source status and ACL editor.
- **Requirements:** KUI-005..KUI-008, KUI-024..KUI-025.
- **Acceptance:** canonical selectors, exact filters, complete/partial sync
  distinction, pagination, keyboard flow and responsive scroll.
- **Rollback:** remove route; retain governed catalog.
- **Stop:** document ingestion controls enter v1.

### KSRCTEST-26 — Ephemeral source test

- **Owns:** administrator source-test command, bounded working set and redacted
  audit.
- **Requirements:** KSRC-032..KSRC-033, KSRC-037..KSRC-040, KLIFE-014.
- **Acceptance:** authorization, normalizer/bounds parity, abort cleanup and
  absence of Conversation/Run/message/evidence/citation rows.
- **Rollback:** disable the command.
- **Stop:** implementation needs an ordinary Run or persistent content.

### KSRCTESTUI-27 — Source-test UX

- **Owns:** query form, progress, bounded result preview and safe errors on the
  source administration page.
- **Requirements:** KUI-009.
- **Acceptance:** keyboard/browser states, untrusted text rendering, no history
  entry and no direct provider request.
- **Rollback:** hide the action.
- **Stop:** browser needs provider IDs or credentials.

### KPROF-28 — Agent Apps Knowledge DTOs

- **Owns:** private draft/history fields and safe public capability projection.
- **Requirements:** KAGT-001..KAGT-015, KUI-014..KUI-015.
- **Acceptance:** additive compatibility, source/profile identity validation,
  public redaction and Knowledge-free Agent regression.
- **Rollback:** stop new binding writes; retain revision data.
- **Stop:** a DTO stores endpoint, credential or raw dataset ID.

### KBUILD-29 — Builder Knowledge editor

- **Owns:** progressive Knowledge section, unique multi-select, profile
  selector and validation summaries.
- **Requirements:** KAGT-001..KAGT-015, KUI-010..KUI-013.
- **Acceptance:** zero/eight/ninth/duplicate flows, retained invalid selection,
  keyboard operation and narrow viewport scroll.
- **Rollback:** hide the section; drafts stay readable.
- **Stop:** Builder infers ACL/readiness instead of rendering server facts.

### KBLDTEST-30 — Builder test admission

- **Owns:** test-scoped definition snapshot and governed Builder Test
  Conversation/Run admission.
- **Requirements:** KAGT-028..KAGT-029, KAGT-031..KAGT-035.
- **Acceptance:** normal authorization/retrieval, builder_test propagation,
  ordinary-history exclusion and lifecycle parity.
- **Rollback:** disable draft test; draft editing remains.
- **Stop:** test execution bypasses a normal Run authority.

### KBLDTESTUI-31 — Builder test UX

- **Owns:** start/cancel/result affordance and clear test-scope labeling.
- **Requirements:** KAGT-028, KAGT-034.
- **Acceptance:** no Market/ordinary-history visibility, keyboard flow, refresh
  isolation and distinct test identity.
- **Rollback:** hide the affordance.
- **Stop:** test results share an ordinary conversation selector.

### KPUB-32 — Publication validation

- **Owns:** source/status/ACL/profile reauthorization and immutable publication
  receipt.
- **Requirements:** KAGT-016..KAGT-027.
- **Acceptance:** concurrent status/ACL/profile changes fail, exact versions
  enter the hash, and Knowledge-free publication remains valid.
- **Rollback:** block publishing drafts with bindings.
- **Stop:** Agent visibility containment is not provable.

### KMARKET-33 — Market authorization and projection

- **Owns:** catalog/detail/deep-link source authorization and safe capability
  summary.
- **Requirements:** KADM-001..KADM-004, KUI-014..KUI-015.
- **Acceptance:** allowed/denied discovery, source-name non-disclosure, ACL
  race, freshness bound and Knowledge-free Agent regression.
- **Rollback:** hide bound Agents; preserve publication.
- **Stop:** Market uses a different ACL evaluator from Run admission.

### KADM-34 — Per-Run reauthorization

- **Owns:** current principal, Agent, source, connection and profile checks for
  new Run and user-visible/orchestrator retry, resume or copy operations that
  create a new Run identity.
- **Requirements:** KADM-005..KADM-017.
- **Acceptance:** each denial path produces zero provider and Engine calls;
  browser-selected source/provider IDs fail before persistence.
- **Rollback:** deny Knowledge-bound Runs.
- **Stop:** current identity or source facts cannot be obtained fail-closed.

### KSNAP-35 — Run snapshot admission

- **Owns:** cross-context receipt and atomic Run/snapshot commit.
- **Requirements:** KADM-018..KADM-025.
- **Acceptance:** exact versions/hash/lifecycle epochs, orphan-free rollback,
  new-Run retry/resume/copy reauthorization, transition-race serialization and
  no dispatch before commit.
- **Rollback:** deny bound Runs; preserve snapshots.
- **Stop:** public APIs cannot support one transaction owner.

### KREXEC-36 — Retrieval fan-out

- **Owns:** attempt claim, one-source calls, configured concurrency, overall
  deadline and cancellation.
- **Requirements:** KRET-001..KRET-015, KRET-049.
- **Acceptance:** parallel limits 1, 4 and 8; eight bound sources; blocking
  provider transport abort and permit release within the typed grace; fixed
  deadline and stale-worker tests.
- **Rollback:** deny bound Runs before retrieval.
- **Stop:** provider calls cannot be bounded by permits and deadline.

### KNORMAPP-37 — Response validation orchestration

- **Owns:** provider-result normalization application flow and safe typed
  failures.
- **Requirements:** KRET-016..KRET-023.
- **Acceptance:** every invalid field fails independently; no partial invalid
  source result reaches ranking or logs.
- **Rollback:** deny provider results.
- **Stop:** application code needs a second provider-specific parser.

### KFUSE-38 — Fusion and evidence commit

- **Owns:** normalized-result deduplication, deterministic RRF, final bounds and
  durable evidence commit.
- **Requirements:** KRET-024..KRET-035, KLIFE-001..KLIFE-003.
- **Acceptance:** deterministic order, count/byte caps, same-attempt ownership
  and stale-generation rollback.
- **Rollback:** deny Engine dispatch; retain evidence.
- **Stop:** fusion output cannot be committed before Engine use.

### KOUTCOME-39 — Knowledge terminal outcomes

- **Owns:** required-source failure, timeout, invalid-response and deterministic
  no-evidence paths.
- **Requirements:** KRET-039..KRET-043, KUI-021..KUI-023.
- **Acceptance:** each outcome has one safe terminal class, truthful user
  action, redacted audit and zero Engine calls where required.
- **Rollback:** deny bound Runs with a safe unavailable outcome.
- **Stop:** a failure could be presented as grounded success.

### KENG-40 — Claude evidence adaptation

- **Owns:** Claude Agent SDK input rendering only.
- **Requirements:** KRET-036..KRET-038.
- **Acceptance:** exact evidence order/IDs, user text once, boundary snapshots,
  prompt-injection fixtures and no provider/private facts.
- **Rollback:** deny bound Runs; retain evidence.
- **Stop:** adapter needs provider SDK objects or credentials.

### KCIT-41 — Citation finalization

- **Owns:** same-Run evidence validation and message/citation atomic
  orchestration.
- **Requirements:** KCIT-001..KCIT-014.
- **Acceptance:** valid, duplicate, cross-Run, stale-generation, over-20,
  idempotent-same and conflicting-duplicate cases.
- **Rollback:** prevent grounded terminal success; preserve data.
- **Stop:** Conversations cannot provide atomic finalization.

### KHYDRATE-42 — Citation history projection

- **Owns:** authorized additive message citations and terminal-hydrate reducer
  contract.
- **Requirements:** KCIT-015..KCIT-023.
- **Acceptance:** history authorization, refresh/reconnect/gap parity, stable
  order, legacy-client compatibility and zero provider reads.
- **Rollback:** omit projection while preserving rows.
- **Stop:** implementation requires a new v4 stream frame.

### KWORKUI-43 — Workspace citation UX

- **Owns:** inline markers, detail drawer and outcome recovery controls.
- **Requirements:** KCIT-021..KCIT-024, KUI-016..KUI-023.
- **Acceptance:** safe untrusted rendering, keyboard/focus, stable order,
  responsive scroll and no browser provider calls.
- **Rollback:** hide citation UI; history remains durable.
- **Stop:** UI needs credentials, provider URLs or direct document fetches.

### KLOG-44 — Log redaction

- **Owns:** structured logging allowlists and error/body sanitization at every
  Knowledge provider boundary.
- **Requirements:** KOPS-014..KOPS-017, KRET-023.
- **Acceptance:** injected query/chunk/key/body canaries are absent from
  captured logs for success, timeout, auth and malformed responses.
- **Rollback:** disable provider calls instead of weakening redaction.
- **Stop:** diagnosis depends on raw provider payloads.

### KMETRIC-45 — Safe metrics

- **Owns:** duration/outcome/source/evidence/permit metrics with bounded labels.
- **Requirements:** KOPS-001..KOPS-006.
- **Acceptance:** cardinality bounds and canary absence; no query, content,
  provider resource ID or credential label.
- **Rollback:** remove optional metrics without changing behavior.
- **Stop:** a metric requires user content as a label.

### KREADY-46 — Readiness

- **Owns:** provider registry/configuration checks and operator readiness
  projection.
- **Requirements:** KOPS-007..KOPS-010.
- **Acceptance:** unknown key fails startup; optional reachability does not;
  active missing/rejected secret reports safe degraded readiness.
- **Rollback:** unregister Knowledge routes/workers.
- **Stop:** startup would call every provider synchronously.

### KRETRY-47 — Permits, retry and deadlines

- **Owns:** per-connection permit pool, typed retry count/base/cap/jitter policy,
  bounded exponential backoff and deadline accounting.
- **Requirements:** KOPS-011..KOPS-013, KOPS-036, KRET-013..KRET-015.
- **Acceptance:** injected clock/random tests for exact retry budget and delay
  bounds, centralized retryability, cancellable permit wait/backoff, deadline
  exhaustion, cancellation, saturation, no duplicate citation and permit release.
- **Rollback:** set retries to zero and preserve bounded single attempts.
- **Stop:** an unknown provider outcome can commit twice.

### KALERT-48 — Alerts and recovery

- **Owns:** typed alert thresholds, state, operator projection, clear rules and
  recovery commands.
- **Requirements:** KOPS-021..KOPS-034.
- **Acceptance:** auth failure, expired sync lease, ratio, timeout and permit
  saturation fault injection; explicit clear and audit tests.
- **Rollback:** retain metrics/readiness and disable optional notifications.
- **Stop:** an alert payload needs raw user/provider content.

### KLIFE-49 — Data lifecycle

- **Owns:** retention settings/status, reference protection and bounded
  working-data cleanup.
- **Requirements:** KLIFE-001..KLIFE-014.
- **Acceptance:** default zero retain, non-zero startup rejection, referenced
  revision protection, test-evidence cleanup and no physical-delete routes.
- **Rollback:** retain all durable Knowledge data fail-safe.
- **Stop:** a cleaner lacks a reference graph and idempotent receipt.

### KCLEANSEED-50 — Stop Skill seed writer

- **Owns:** only the RAGFlow Skill, immutable version and default distribution
  seed statements named in baseline-disposition.md.
- **Requirements:** KMIG-006..KMIG-009, KMIG-013..KMIG-014.
- **Acceptance:** Skill/version/distribution reference inventory, no new seed
  writes, rolling-reader compatibility and exact post-delete absence.
- **Rollback:** restore only these Skill seed statements for a named active
  materialization consumer.
- **Stop:** an active or retryable Run still needs the seeded Skill release.

### KCLEANMCPSEED-60 — Stop MCP tool/policy seed writer

- **Owns:** only the RAGFlow MCP tool and default tool-policy seed statements
  named in baseline-disposition.md.
- **Requirements:** KMIG-004, KMIG-006..KMIG-008, KMIG-013..KMIG-014.
- **Acceptance:** exact MCP/policy consumer inventory, generic MCP regression,
  no new seed writes and rolling-reader compatibility.
- **Rollback:** restore only the read-only MCP/policy seed for a named active
  external consumer.
- **Stop:** an admitted Run or external admin client still uses the built-in
  tool identity.

### KCLEANAGTSEED-61 — Stop SOP Agent seed writer

- **Owns:** only the sop-assistant Agent seed statement named in
  baseline-disposition.md.
- **Requirements:** KMIG-006..KMIG-009, KMIG-013..KMIG-014.
- **Acceptance:** replacement Agent/revision is recorded, published/conversation
  references are inventoried, new seed writes stop, and historical labels stay
  readable.
- **Rollback:** restore only the Agent seed for a named active deep-link or
  conversation consumer.
- **Stop:** replacement source binding, public alias or historical-reader
  decision is missing.

### KCLEANSKILL-51 — Retire active Skill selection

- **Owns:** seeded Skill active catalog/dependency/replay special cases and its
  active asset exposure.
- **Requirements:** KAGT-001, KMIG-006..KMIG-009.
- **Acceptance:** no new selection, active/retryable reference counts, archived
  materialization decision and owning test migration.
- **Rollback:** restore historical reader/asset only.
- **Stop:** a supported Run still needs materialization.

### KCLEANMCP-52 — Retire MCP built-in special cases

- **Owns:** RAGFlow trusted-builtin constants, SQL ordering/authority branches
  and seeded tool policy behavior.
- **Requirements:** KMIG-004..KMIG-006, KMIG-010.
- **Acceptance:** generic MCP regression remains green; no bound Run/tool
  consumer; RAGFlow special-case source absence.
- **Rollback:** restore a read-only MCP compatibility reader for a named row.
- **Stop:** a current external client still uses the built-in tool identity.

### KCLEANROUTE-53 — Retire active hard-coded routing

- **Owns:** knowledge_answer static capability, intent and Chat selection
  branches for the seeded Agent/Skill.
- **Requirements:** KMIG-006, KMIG-008, KMIG-010.
- **Acceptance:** Agent detail and pinned revision own selection; copy/retry/
  resume/deep-link regressions; no provider-specific route branch.
- **Rollback:** restore an alias-to-Agent translator only.
- **Stop:** a public alias has no migration decision.

### KCOMPAT-54 — Historical readers and sanitizers

- **Owns:** each baseline alias, context marker, public-payload sanitizer and
  terminal error mapping according to its explicit disposition.
- **Requirements:** KMIG-007..KMIG-009, KMIG-014.
- **Acceptance:** reader/writer matrix, retained payload fixtures and proof that
  any removed reader is unreachable.
- **Rollback:** restore the exact reader without restoring a writer.
- **Stop:** retained data contents are unknown.

### KCLEANDEPLOY-55 — Deployment and readiness cleanup

- **Owns:** the empty deploy comment, obsolete readiness selectors and test
  ownership changes named in the baseline.
- **Requirements:** KMIG-006, KMIG-014.
- **Acceptance:** packaging parser proof, current readiness selects runnable
  Knowledge tests, and the retired-setting regression remains.
- **Rollback:** documentation/test-selector restore only.
- **Stop:** a deployment consumer parses the retired text.

### KCI-56 — CI and packaged-image gates

- **Owns:** changed-path required aggregator and Knowledge source/build/image
  acceptance wiring.
- **Requirements:** KOPS-018..KOPS-020.
- **Acceptance:** architecture, migration, required inventory, unit, real
  PostgreSQL, Run, frontend, public contract, secret/redaction and image gates
  all report the exact SHA; upstream manifests plus KCI-56's own manifest contain
  every P0 KAC-FR identity through packaged-image evidence. KOPS-020 remains in
  the later KACCEPT-58 external-runtime manifest.
- **Rollback:** revert CI wiring only; no gate becomes optional.
- **Stop:** a high-risk path has no required job.

### KCONCUR-57 — Fifty-Run capacity acceptance

- **Owns:** deterministic provider fixture and controlled capacity procedure;
  no production behavior.
- **Requirements:** KRET-014..KRET-015, KOPS-004..KOPS-006.
- **Acceptance:** KAC-CONC-001..KAC-CONC-010 at exactly 50 concurrent
  authorized Runs, separately from company-provider capacity.
- **Rollback:** none.
- **Stop:** exact image, limits, database pool or result bindings are absent.

### KACCEPT-58 — Company RAGFlow runtime acceptance

- **Owns:** exact-subject runtime/browser procedure and redacted evidence only.
- **Requirements:** KOPS-020 and all P0 user journeys.
- **Acceptance:** KAC-RT-001..KAC-RT-016 plus cleanup against
  one exact image, provider, principal, Agent revision, Run and citation set.
- **Rollback:** release owner uses the existing image rollback procedure.
- **Stop:** any subject identity or provider authorization is uncertain.

### KAGENTIC-59 — Agent-directed retrieval follow-on

- **Owns:** generic knowledge_search execution capability delegating to the
  same authorized Knowledge operation.
- **Requirements:** KAGT-030, KRET-044..KRET-045.
- **Acceptance:** model cannot expand source/profile scope or submit provider
  identities; evidence/citations reuse the deterministic contract.
- **Rollback:** disable execution mode; deterministic mode remains.
- **Stop:** implementation would create another Knowledge authority.

## 5. Release ordering

KTRACE-62 activates the atomic-case manifest gate before KADR-01. No product
code begins before that gate is required and KADR-01 is accepted. Schema slices
may proceed in dependency order after KDOM-03 and their pure prerequisites.
Cleanup slices begin only after KINV-02 is refreshed at the exact cleanup base
and the named replacement is accepted. KCONCUR-57 proves platform-controlled
capacity; KACCEPT-58 separately proves the deployed company provider path. A
merged pull request, green CI or running image does not substitute for the next
evidence layer.
