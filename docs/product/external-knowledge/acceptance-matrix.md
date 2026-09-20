# External Knowledge Acceptance Matrix

## 1. Evidence boundary

This matrix separates source, CI/build, deployment, runtime verification and
external acceptance. A source test, mock provider, green CI job, built image or
merged pull request does not prove connectivity to the company's RAGFlow
deployment.

Every implementation slice owns the smallest falsifiable checks named in
[`implementation-slices.md`](implementation-slices.md). Every functional
requirement has an owning atomic case and slice in
[`traceability-matrix.md`](traceability-matrix.md). The final release also
requires one controlled external-acceptance packet for the exact deployed
subject.

### 1.1 Atomic requirement cases

For every requirement ID R in functional-requirements.md, the corresponding
row in traceability-matrix.md defines one atomic case named KAC-FR-R whose
assertion is exactly that single requirement statement. That row is the source
acceptance manifest before implementation exists. Before KADR-01 or any later
External Knowledge slice merges, each requirement owned by that slice must
expose its atomic case ID in its machine-readable test manifest. KTRACE-62 is
the bootstrap slice: its pull request contains the schema, its own manifest
fixture, validator and focused tests, and makes the required check active for
KADR-01 before product code begins. KCI-56 aggregates every P0 case through the
packaged-image evidence layer; KACCEPT-58 separately owns the required external-
runtime P0 case. A broader scenario case cannot replace a missing atomic case.

## 2. Required subject binding

One external-acceptance packet binds every observation to the same subject:

| Field | Required value |
| --- | --- |
| Source | Exact 40-character merged `main` commit |
| Image | Deployed immutable platform image digest matching Source |
| Provider deployment | Redacted RAGFlow version or immutable image identity |
| Connection | Logical connection ID and active revision ID |
| Principal | Redacted user identity, departments, roles and policy version |
| Agent | `agent_id`, immutable revision and `content_hash` |
| Sources | Ordered logical source IDs and ACL versions |
| Retrieval policy | Retrieval profile ID and immutable revision |
| Conversation | Exact Agent Conversation ID |
| Execution | Exact submission, Run, attempt and trace correlation |
| Evidence | Retrieval attempt ID, accepted evidence count and evidence digests |
| Message | Durable assistant message ID |
| Citations | Ordered citation IDs and citation digests |

Provider credentials, raw provider URLs, provider dataset IDs, private
instructions, raw queries, raw provider response/chunk payloads, commands,
storage keys and Engine-private payloads must not enter committed evidence.
Only the normalized, bounded passage text and allowlisted metadata defined by
the evidence schema may persist.

## 3. Source contract acceptance

### 3.1 Architecture and placement

| Case | Required observation |
| --- | --- |
| KAC-ARC-001 | The accepted ADR names Knowledge as the single product authority for connections, sources, source ACL, retrieval policy, evidence and citations. |
| KAC-ARC-002 | The source architecture permits only `knowledge.api` and `knowledge.events` as cross-domain product boundaries. |
| KAC-ARC-003 | Generic MCP remains a separate context and contains no provider-specific Knowledge policy. |
| KAC-ARC-004 | Provider adapters are registered only by bootstrap through one typed registry. |
| KAC-ARC-005 | Routes contain no provider HTTP client calls. |
| KAC-ARC-006 | Engine adapters contain no credential resolution. |
| KAC-ARC-007 | Architecture tests reject RAGFlow conditionals in frozen global route and repository modules. |
| KAC-ARC-008 | The accepted ADR distinguishes Knowledge provider secret resolution from Engine/model provider credentials. |
| KAC-ARC-009 | Runtime and source authority maps preserve one enterprise identity scope and add no configurable or user-facing tenant boundary. |

### 3.2 Domain unit tests

| Case | Required observation |
| --- | --- |
| KAC-DOM-001 | Connection status transitions accept every declared transition and reject every undeclared transition. |
| KAC-DOM-002 | Source status transitions accept every declared transition and reject every undeclared transition. |
| KAC-DOM-003 | Retrieval-attempt transitions reject stale generation and post-terminal mutation. |
| KAC-DOM-004 | Retrieval-profile validation enforces every numeric range and byte bound. |
| KAC-DOM-005 | Source ACL evaluation matches existing Agent ACL department, role and user semantics. |
| KAC-DOM-006 | Restricted ACL defaults deny an ordinary principal with no matching grant. |
| KAC-DOM-007 | Publication compatibility rejects an Agent visibility broader than any required source. |
| KAC-DOM-008 | Ordered Agent source bindings produce a deterministic content hash. |
| KAC-DOM-009 | Duplicate logical source selection is rejected. |
| KAC-DOM-010 | A ninth source selection is rejected. |
| KAC-DOM-011 | Provider response normalization rejects every required-field violation independently. |
| KAC-DOM-012 | Provider result deduplication uses the declared composite identity. |
| KAC-DOM-013 | Reciprocal-rank fusion is deterministic for equal ranks and scores. |
| KAC-DOM-014 | Fusion enforces the final evidence count. |
| KAC-DOM-015 | Fusion enforces the total evidence byte budget. |
| KAC-DOM-016 | No-result normalization returns `no_evidence`. |
| KAC-DOM-017 | Citation finalization rejects a cross-Run evidence ID. |
| KAC-DOM-018 | Citation finalization rejects a duplicate evidence ID. |
| KAC-DOM-019 | Citation finalization rejects more than 20 evidence IDs. |
| KAC-DOM-020 | Citation snapshot projection omits every private provider field. |
| KAC-DOM-021 | A new Agent revision defaults `knowledge_enabled` to false, persists the explicit flag in its immutable hash, and never derives it from retained source selections. |

### 3.3 Provider contract tests

The RAGFlow adapter tests run against a deterministic HTTP server that records
requests and returns fixed response fixtures. Each case verifies request bytes,
timeouts, response normalization and safe errors.

| Case | Required observation |
| --- | --- |
| KAC-PRV-001 | Connection check sends Bearer authentication to the bounded dataset-list request. |
| KAC-PRV-002 | Anonymous health success alone does not pass the connection check. |
| KAC-PRV-003 | Redirect to a disallowed origin fails before credential forwarding. |
| KAC-PRV-004 | Invalid TLS fails with a safe connection error. |
| KAC-PRV-005 | Catalog pagination visits every provider page exactly once. |
| KAC-PRV-006 | Retrieval sends one server-resolved dataset identity per source call. |
| KAC-PRV-007 | Retrieval maps the declared question, per-source result limit to `page_size`, candidate-pool size to `knn_top_k`, and score threshold without sending deprecated `top_k`. |
| KAC-PRV-008 | Retrieval does not send a browser or model-provided dataset identity. |
| KAC-PRV-009 | A valid native chunk maps to the complete provider-neutral record. |
| KAC-PRV-010 | A mismatched response dataset identity is rejected. |
| KAC-PRV-011 | Missing content is rejected. |
| KAC-PRV-012 | Missing document identity is rejected. |
| KAC-PRV-013 | Missing promised chunk identity is rejected. |
| KAC-PRV-014 | Non-finite similarity is rejected. |
| KAC-PRV-015 | Oversized content is deterministically bounded. |
| KAC-PRV-016 | Unknown response fields do not enter the normalized projection. |
| KAC-PRV-017 | A per-call timeout before the overall deadline maps to `knowledge_provider_transient`; exhausting the fixed overall deadline maps to `knowledge_retrieval_timeout`. |
| KAC-PRV-018 | Authentication rejection maps to a safe provider-neutral code. |
| KAC-PRV-019 | Provider error bodies do not enter the public error. |
| KAC-PRV-020 | Provider error bodies do not enter captured logs. |
| KAC-PRV-021 | Only connect reset, per-call timeout and HTTP 429/502/503/504 map to the centralized transient retry outcome; authentication, authorization, invalid response, TLS, egress, binding and unknown failures do not retry. |
| KAC-PRV-022 | A blocking provider acknowledges transport close and releases its permit within the configured cancellation grace. |

### 3.4 PostgreSQL integration tests

These cases use the repository's real PostgreSQL integration stage.

| Case | Required observation |
| --- | --- |
| KAC-DB-001 | Additive migration applies through the canonical migration runner. |
| KAC-DB-002 | Reapplying the migration is idempotent. |
| KAC-DB-003 | Connection revision is immutable after insert. |
| KAC-DB-004 | Logical source uniqueness is enforced by connection and provider resource identity. |
| KAC-DB-005 | ACL replacement creates a new authorization version without rewriting the prior version. |
| KAC-DB-006 | Concurrent source synchronization upserts one logical source. |
| KAC-DB-007 | A partial sync transaction leaves the previous complete catalog authoritative. |
| KAC-DB-008 | A stale sync lease cannot mark sources missing. |
| KAC-DB-009 | Agent publication stores the ordered binding set and exact versions. |
| KAC-DB-010 | Run admission stores one snapshot atomically with the admitted Run. |
| KAC-DB-011 | Run admission rollback leaves no orphan Knowledge snapshot. |
| KAC-DB-012 | Stale retrieval generation cannot persist evidence. |
| KAC-DB-013 | Duplicate evidence finalization returns one citation set. |
| KAC-DB-014 | Conflicting duplicate finalization fails without modifying citations. |
| KAC-DB-015 | Message and citation finalization commit together. |
| KAC-DB-016 | Message and citation finalization roll back together. |
| KAC-DB-017 | Disabling a source preserves historical citations. |
| KAC-DB-018 | Marking a source missing preserves historical citations. |
| KAC-DB-019 | Every persisted JSON/text field rejects its declared oversize value before write. |
| KAC-DB-020 | Required indexes support connection catalog, source list, Agent binding, Run snapshot and message-citation reads. |
| KAC-DB-021 | Synchronization job state, operation identity, retry link, cursor and lease generation survive process restart. |
| KAC-DB-022 | A lease-expired or unknown-commit synchronization enters `reconcile_required` and cannot commit later. |
| KAC-DB-023 | Successful catalog commit writes its receipt and removes candidate rows atomically. |
| KAC-DB-024 | Accepted evidence is durable before Engine dispatch and remains immutable after retrieval success. |
| KAC-DB-025 | Evidence remains readable by duplicate delivery, worker recovery, Engine continuation and citation finalization for the retained owning Run. |
| KAC-DB-026 | A checked candidate connection revision cannot activate before a complete synchronization bound to that exact revision commits. |
| KAC-DB-027 | A failed, partial or stale candidate-revision synchronization leaves the prior active revision, catalog generation and freshness unchanged. |
| KAC-DB-028 | Concurrent Run admission and connection disable/supersession serialize on the connection epoch: an admission that commits first may finish only its exact current attempt before deadline, while a transition that commits first causes admission to reauthorize or fail with zero provider calls. |
| KAC-DB-029 | Two multi-connection admissions that present reverse binding order acquire source then connection rows by ascending ID, complete without deadlock, and re-read every authority fact after locking. |
| KAC-DB-030 | Catalog commit or candidate activation racing a multi-connection admission uses the same source-then-connection ascending-ID order, rechecks fences after locking and completes without deadlock or stale snapshot. |
| KAC-DB-031 | Draft, initial checking and initial cataloging remain at epoch zero with no receipt; initial activation writes epoch one; replacement candidate check/catalog work leaves an active, unavailable or disabled serving state and epoch unchanged; each guarded final serving-state or active-pair transition writes one next-epoch immutable receipt. |
| KAC-DB-032 | Successful terminal compare-and-set commits its complete bounded evidence set atomically; cancellation, timeout or a losing terminal writer commits no additional evidence. |

### 3.5 Authorization and admission integration tests

| Case | Required observation |
| --- | --- |
| KAC-AUTH-001 | A principal allowed by Agent and all required sources may discover the Agent. |
| KAC-AUTH-002 | A principal denied by Agent ACL cannot discover the Agent. |
| KAC-AUTH-003 | A principal denied by one required source cannot discover the Agent. |
| KAC-AUTH-004 | A denied principal cannot infer the restricted source name from Market or detail errors. |
| KAC-AUTH-005 | Publish rejects an inactive source. |
| KAC-AUTH-006 | Publish rejects an ACL-incompatible source. |
| KAC-AUTH-007 | Draft save preserves an unavailable existing selection for corrective display. |
| KAC-AUTH-008 | New Run reauthorization observes a department removal. |
| KAC-AUTH-009 | User-visible retry creates a new Run and its reauthorization observes a source disable. |
| KAC-AUTH-010 | User-visible resume creates a new Run and its reauthorization observes a source ACL change. |
| KAC-AUTH-011 | Copy creates a new Run and its reauthorization observes a connection disable. |
| KAC-AUTH-012 | Any pre-dispatch denial produces zero provider requests. |
| KAC-AUTH-013 | Any pre-dispatch denial produces zero Engine calls. |
| KAC-AUTH-014 | Browser-submitted source IDs are ignored or rejected before persistence. |
| KAC-AUTH-015 | Browser-submitted provider IDs are rejected before persistence. |
| KAC-AUTH-016 | Browser-submitted department or role values never grant source access. |
| KAC-AUTH-017 | Publish rejects an enabled revision with an incomplete source/profile selection, while a disabled revision performs no Knowledge authorization and persists no executable bindings. |

### 3.6 Execution and message integration tests

| Case | Required observation |
| --- | --- |
| KAC-RUN-001 | The worker restores the exact Run Knowledge Snapshot before retrieval. |
| KAC-RUN-002 | Parallel limits of 1, 4 and 8 are enforced, and eight bound sources never create more calls than the configured maximum. |
| KAC-RUN-003 | Attempt claim persists one fixed `deadline_at`; an injected clock and random source prove the typed retry count/base/cap/jitter policy cannot extend it, while a blocking fake provider proves transport abort and permit release complete within `cancellation_grace_ms`. |
| KAC-RUN-004 | A required source failure prevents Engine dispatch. |
| KAC-RUN-005 | A valid no-evidence result produces the deterministic user message without Engine dispatch. |
| KAC-RUN-006 | Accepted evidence reaches the Claude adapter in deterministic order. |
| KAC-RUN-007 | Claude input contains no provider URL, credential or raw dataset identity. |
| KAC-RUN-008 | Claude input distinguishes evidence, user text and system instructions. |
| KAC-RUN-009 | A final answer can cite only same-Run evidence IDs. |
| KAC-RUN-010 | A successful grounded answer has durable citations before terminal success. |
| KAC-RUN-011 | A citation finalization failure prevents grounded terminal success. |
| KAC-RUN-012 | Cancel during retrieval signals and aborts in-flight transport, starts no new retry or provider call, releases permits within the typed grace and records `cancelled`. |
| KAC-RUN-013 | A stale worker cannot complete a successor retrieval generation. |
| KAC-RUN-014 | Concurrent user cancellation and deadline expiry release provider work first, then one fenced terminal compare-and-set chooses `cancelled` when the persisted cancellation is no later than the deadline and timeout failure otherwise; the loser is a no-op. |
| KAC-RUN-015 | Retry fault injection proves only the centralized transient outcome consumes the exact retry budget, and cancellable permit wait/backoff cannot cross the fixed overall deadline or start another call. |
| KAC-RUN-016 | A disabled Agent Run creates no Run Knowledge Snapshot or provider request and continues through the ordinary non-Knowledge Engine path. |

### 3.7 Public projection and streaming tests

| Case | Required observation |
| --- | --- |
| KAC-PUB-001 | Public Agent payload reports only enabled, source count and bounded freshness. |
| KAC-PUB-002 | Public Agent payload contains no source ID, provider ID, URL, key, ACL or retrieval internals. |
| KAC-PUB-003 | Canonical message history includes authorized citation projections in stable order. |
| KAC-PUB-004 | Unauthorized message history cannot read citation projections. |
| KAC-PUB-005 | `run.succeeded` remains terminal-hydrate authority. |
| KAC-PUB-006 | Terminal hydrate replaces provisional message state with one citation set. |
| KAC-PUB-007 | SSE reconnect does not duplicate citation state. |
| KAC-PUB-008 | A replay gap followed by hydrate restores the same citation order. |
| KAC-PUB-009 | Stream projections contain no raw query or chunk text before durable hydrate. |
| KAC-PUB-010 | Legacy clients can ignore the additive citation field. |
| KAC-PUB-011 | A disabled Agent projects `enabled=false`, `source_count=0` and null freshness even when administrative authoring selections are retained. |

### 3.8 Administrative authorization

| Case | Required observation |
| --- | --- |
| KAC-MGMT-001 | A platform connection administrator can create, update, check, activate the server-held candidate and disable a connection. |
| KAC-MGMT-002 | A knowledge administrator may read a safe connection projection but cannot create, update, check, activate a candidate or disable it. |
| KAC-MGMT-003 | An ordinary principal cannot read or mutate an administrative connection and cannot infer whether a denied target exists. |
| KAC-MGMT-004 | A knowledge administrator can start/retry synchronization and update source presentation/status. |
| KAC-MGMT-005 | A non-knowledge administrator cannot start synchronization, mutate source presentation/status or replace source ACL. |
| KAC-MGMT-006 | Every administrative mutation enforces caller operation identity and rejects same identity with different input. |
| KAC-MGMT-007 | A denied source or ACL mutation does not reveal source name, provider identity or ACL contents. |
| KAC-MGMT-008 | Server-owned scheduler synchronization uses the same application command and cannot call the repository or provider directly. |
| KAC-MGMT-009 | Source listing enforces hard limit and opaque cursor without unbounded browser accumulation. |
| KAC-MGMT-010 | Exact connection filter, status filter and bounded name search each return only matching logical sources. |
| KAC-MGMT-011 | Source list/detail projections expose logical source IDs and omit provider resource IDs. |
| KAC-MGMT-012 | Only an authorized source manager may invoke source retrieval testing. |
| KAC-MGMT-013 | Updating a connection rejects a canonical base-URL or provider-key change and directs the administrator to create a new connection. |
| KAC-MGMT-014 | Connection and Market freshness become current only from the complete synchronization bound to the active revision. |

### 3.9 Test-operation isolation

| Case | Required observation |
| --- | --- |
| KAC-TEST-001 | An authorized source test returns the same bounded normalized fields as Run retrieval. |
| KAC-TEST-002 | Completing a source test creates no Conversation, Run, message, durable evidence or citation row. |
| KAC-TEST-003 | Cancelling or timing out a source test releases its bounded working evidence. |
| KAC-TEST-004 | Source-test audit contains only logical IDs, count, duration and safe outcome. |
| KAC-TEST-005 | A Builder draft test uses one Builder Test Conversation and a normal governed Run. |
| KAC-TEST-006 | Builder-test Run, evidence, message and citation records all carry the `builder_test` scope. |
| KAC-TEST-007 | Builder-test messages and citations are absent from Market and ordinary-user history. |
| KAC-TEST-008 | Builder-test retry creates another test-scoped Run; retention and authorization match each owning test Run and message. |

### 3.10 Data lifecycle

| Case | Required observation |
| --- | --- |
| KAC-LIFE-001 | Knowledge retention defaults to zero and the status projection reports retain/fail-safe behavior. |
| KAC-LIFE-002 | A non-zero Knowledge retention setting fails startup until a reference-safe cleaner is accepted. |
| KAC-LIFE-003 | Referenced connection revisions and ACL versions cannot be physically deleted. |
| KAC-LIFE-004 | After disable or supersession, only the exact current attempt whose snapshot epoch has an immutable lifecycle receipt binding the pinned revision may resolve its credential before the existing deadline; user-visible retry, resume and copy create new Runs and reauthorize, while historical reads remain intact. |
| KAC-LIFE-005 | Citation retention follows its assistant message and evidence retention follows its Run. |
| KAC-LIFE-006 | The v1 route inventory contains no physical-delete endpoint for connection, source, evidence or citation. |
| KAC-LIFE-007 | No Knowledge row mutation implicitly destroys a secret. |
| KAC-LIFE-008 | Synchronization candidate rows disappear only after a durable terminal receipt. |

### 3.11 Operations alerts and recovery

| Case | Required observation |
| --- | --- |
| KAC-OPS-001 | A missing or rejected secret on an active connection raises `knowledge_connection_auth_invalid`. |
| KAC-OPS-002 | An expired synchronization lease raises `knowledge_sync_reconcile_required`. |
| KAC-OPS-003 | Failure and timeout ratio alerts respect their window, minimum sample count and threshold. |
| KAC-OPS-004 | Permit-saturation alert uses p95 wait duration without unbounded labels. |
| KAC-OPS-005 | Alert payloads contain no URL, API key, query, chunk or provider response body. |
| KAC-OPS-006 | A connection alert clears only after a later authenticated check succeeds. |
| KAC-OPS-007 | A synchronization alert clears only after successor success or explicit operator disposition. |
| KAC-OPS-008 | Operator retry uses a new operation identity linked to the prior synchronization job. |
| KAC-OPS-009 | Retry, disable and alert disposition each produce a redacted audit fact. |

### 3.12 Migration and deletion proof

| Case | Required observation |
| --- | --- |
| KAC-MIG-001 | Cleanup inventory is refreshed against the exact pull-request base. |
| KAC-MIG-002 | Persisted Skill, version, Agent, MCP, policy, revision, Run, message and event counts are recorded before cleanup. |
| KAC-MIG-003 | Every retired writer names its canonical replacement and proves new writes are zero. |
| KAC-MIG-004 | Every retained compatibility reader has a stored/external consumer fixture. |
| KAC-MIG-005 | Removing one reader includes a post-delete absence proof and rolling-reader regression. |
| KAC-MIG-006 | Generic MCP, Skill, Agent, Run, Conversation and SSE authorities remain intact. |
| KAC-MIG-007 | The retired global RAGFlow setting names remain rejected after the new connection model is added. |

### 3.13 Requirement-manifest governance

| Case | Required observation |
| --- | --- |
| KAC-CI-001 | The per-slice validator derives exclusive ownership from the traceability matrix and rejects a missing, extra, duplicate, unknown or differently owned atomic case ID in the candidate manifest. |

## 4. Frontend component and browser acceptance

### 4.1 Component tests

| Case | Required observation |
| --- | --- |
| KAC-UI-001 | Credential input never repopulates from a read response. |
| KAC-UI-002 | Connection check shows checking, success and safe failure states. |
| KAC-UI-003 | Catalog pagination does not append an unbounded dataset list. |
| KAC-UI-004 | Source ACL editor uses canonical department, role and user selectors. |
| KAC-UI-005 | Builder multi-select enforces eight unique sources. |
| KAC-UI-006 | Builder separates Knowledge, Skill Set and MCP sections. |
| KAC-UI-007 | Builder displays unavailable retained selections with corrective guidance. |
| KAC-UI-008 | Publish displays ACL incompatibility without provider internals. |
| KAC-UI-009 | Citation markers map to ordered citation projections. |
| KAC-UI-010 | Citation drawer renders title, excerpt, relevance and position safely. |
| KAC-UI-011 | Citation content is rendered as untrusted text. |
| KAC-UI-012 | No-evidence and unavailable outcomes have distinct recovery actions. |
| KAC-UI-013 | Source-test states render bounded untrusted evidence and never create a history item. |
| KAC-UI-014 | Builder test is visibly test-scoped and cannot be mistaken for an ordinary conversation. |
| KAC-UI-015 | A new or disabled expert performs no Knowledge catalog request; enabling the accessible switch loads the catalog once and makes source/profile selection mandatory. |

### 4.2 Browser checks

| Case | Required observation |
| --- | --- |
| KAC-BR-001 | Desktop and narrow viewport Knowledge Connections remain scrollable and actionable. |
| KAC-BR-002 | Desktop and narrow viewport Knowledge Sources preserve pagination and filters. |
| KAC-BR-003 | Keyboard-only administration can create, check and disable a connection. |
| KAC-BR-004 | Keyboard-only Builder can select and remove multiple sources. |
| KAC-BR-005 | An authorized ordinary user sees the knowledge-backed Agent in Market. |
| KAC-BR-006 | A denied ordinary user cannot discover or deep-link the Agent. |
| KAC-BR-007 | Opening the Workspace creates no conversation. |
| KAC-BR-008 | Explicit send creates one revision-pinned conversation and one Run. |
| KAC-BR-009 | A completed answer renders stable inline citations and detail drawer. |
| KAC-BR-010 | Refresh preserves answer text and citation order. |
| KAC-BR-011 | Simulated reconnect preserves answer text and citation order. |
| KAC-BR-012 | No browser request is sent directly to the provider origin. |
| KAC-BR-013 | Keyboard-only source testing can start, cancel and inspect bounded results. |
| KAC-BR-014 | Builder test refresh remains isolated from ordinary conversation history. |
| KAC-BR-015 | Keyboard-only Builder can toggle Enterprise Knowledge per expert and observes the same persisted state after refresh. |

## 5. CI and packaging gates

The release candidate requires:

1. exact per-slice requirement-manifest validation;
2. architecture policy and source-placement checks;
3. schema migration and status checks;
4. Knowledge unit, contract and real-PostgreSQL integration stages;
5. Agent Apps publication and Runs admission regression stages;
6. execution adapter and v4 streaming regression stages;
7. frontend typecheck, lint, component tests and production build;
8. generated public-contract consistency when projections change;
9. secret and ordinary-user redaction checks; and
10. image build and supply-chain checks owned by packaging.

These gates establish source, CI and packaged evidence only.

## 6. Controlled runtime verification

Runtime verification uses the exact deployed platform image and the configured
company RAGFlow connection. The release owner observes:

| Case | Runtime observation |
| --- | --- |
| KAC-RT-001 | API and worker readiness identify a valid registered RAGFlow provider and schema version. |
| KAC-RT-002 | Authenticated connection check reaches the configured provider and lists an allowed dataset page. |
| KAC-RT-003 | Complete synchronization projects the expected redacted source count. |
| KAC-RT-004 | One authorized source test returns bounded evidence with stable provider document and chunk identities. |
| KAC-RT-005 | One Agent revision publishes with two logical sources and one retrieval profile. |
| KAC-RT-006 | One authorized ordinary-user Run returns a cited grounded answer. |
| KAC-RT-007 | One denied principal receives no Agent discovery and produces no provider request. |
| KAC-RT-008 | One no-match query returns the deterministic no-evidence state. |
| KAC-RT-009 | One controlled provider timeout produces the safe unavailable state. |
| KAC-RT-010 | Refresh and reconnect restore the same citations from PostgreSQL without a provider read. |
| KAC-RT-011 | Disabling the source denies a new Run while preserving the prior cited history. |
| KAC-RT-012 | Re-enabling and reauthorizing the source restores new-Run availability. |
| KAC-RT-013 | The source-test operation leaves no Conversation, Run, message, evidence or citation row after completion. |
| KAC-RT-014 | One Builder test remains test-scoped and absent from ordinary Market/history projections. |
| KAC-RT-015 | One expired synchronization lease enters reconcile-required and a linked successor completes safely. |
| KAC-RT-016 | One injected connection authentication failure raises and then clears the safe operator alert. |

## 7. Concurrency acceptance

The platform target is 50 concurrent knowledge-backed Agent Runs. This target
does not assert the capacity of the company's RAGFlow deployment until measured.

### 7.1 Platform-controlled test

Against a deterministic latency-injected provider service, run 50 concurrent
authorized Runs and verify:

| Case | Required observation |
| --- | --- |
| KAC-CONC-001 | Exactly 50 authorized Runs are admitted against the same declared image and deterministic provider fixture. |
| KAC-CONC-002 | No evidence crosses principal, Agent, source or Run boundaries. |
| KAC-CONC-003 | Per-Run parallelism respects configured limits of 1, 4 and 8. |
| KAC-CONC-004 | Per-connection outbound concurrency never exceeds the operator permit limit. |
| KAC-CONC-005 | No Run produces duplicate retrieval attempts, messages or citation sets. |
| KAC-CONC-006 | PostgreSQL pool usage and worker queue depth remain within declared bounds. |
| KAC-CONC-007 | Cancellation releases every provider permit within the declared tolerance. |
| KAC-CONC-008 | Every Run reaches exactly one truthful terminal outcome. |
| KAC-CONC-009 | p50, p95 and maximum admission, permit-wait and retrieval durations are recorded. |
| KAC-CONC-010 | The result explicitly separates platform capacity from company RAGFlow capacity. |

### 7.2 Provider-controlled test

Against the company RAGFlow deployment, the authorized owner chooses a safe
test window and predeclares dataset, query corpus, duration, rate and stop
thresholds. Results are capacity evidence for that exact provider subject only.
If the provider cannot support 50 simultaneous retrievals, platform admission
must queue or rate-limit rather than create unbounded work.

## 8. External acceptance decision

External acceptance is `passed` only when every required runtime case is bound
to one exact subject and cleanup succeeds. A missing provider identity,
credential uncertainty, incomplete source ACL proof, citation mismatch,
provider-capacity uncertainty or absent browser observation is
`EVIDENCE_BLOCKED` or `UNKNOWN`, never inferred success.
