# External Knowledge Domain and API Contract

## 1. Authority and dependency boundary

The product requires one `knowledge` bounded context in the domain-first
modular monolith. It owns provider connections, logical sources, source ACL,
retrieval profiles, retrieval admission, provider normalization, evidence
receipts and citation snapshots. Product code remains gated until an accepted
ADR updates the repository's current MCP-owned external-knowledge authority.

The target dependency direction is:

```text
knowledge.transport -> knowledge.application -> knowledge.domain -> kernel
knowledge.infrastructure --------------------^          |
knowledge.infrastructure -> platform technical clients--+

agent_apps.application -> knowledge.api
runs.application       -> knowledge.api
execution.application  -> knowledge.api
streaming consumes committed safe facts through knowledge.events
bootstrap registers knowledge provider adapters
```

The Knowledge context does not own Agent publication, conversations, messages,
Run terminal state, SSE transport, generic MCP authorization, company identity,
provider document preparation or Engine execution.

The prerequisite ADR must also distinguish model/Engine provider credentials
from external-Knowledge provider credentials. Knowledge owns the credential
reference and the right to request resolution for an admitted provider call;
the shared secret infrastructure owns credential bytes and physical secret
lifecycle.

## 2. Ubiquitous language

**Knowledge Connection**
: A versioned, administrator-owned configuration that identifies one external
  knowledge provider endpoint and one server-owned credential reference.

**Connection Revision**
: An immutable provider endpoint, provider key, credential reference and typed
  transport-policy snapshot. Activation follows an authenticated capability
  check.

**Knowledge Source**
: A platform logical identity projecting one provider-owned dataset. It has a
  local display identity, lifecycle and ACL while retaining a private provider
  resource binding.

**Provider Resource ID**
: The private dataset identity used only by the provider adapter. It is not a
  browser selector or public projection.

**Source Authorization Version**
: The immutable version of a Knowledge Source ACL used by publication and Run
  admission.

**Retrieval Profile**
: A versioned platform policy containing retrieval mode, provider result limit,
  score threshold, fusion, deadlines and evidence bounds.

**Run Knowledge Snapshot**
: The immutable Run receipt binding Agent revision, admitted logical sources,
  source authorization versions, active connection revisions and retrieval
  profile version.

**Retrieval Attempt**
: One generation-fenced application operation that queries every admitted
  source, validates responses and constructs an evidence set.

**Evidence Item**
: One bounded, typed, provider-derived passage accepted for the current Run and
  assigned a platform evidence ID.

**Citation Snapshot**
: A durable, bounded projection of evidence actually referenced by one durable
  assistant message. It remains readable from authorized history without a
  provider call.

**Grounded Answer**
: A durable assistant message whose cited evidence IDs all resolve to accepted
  evidence from the same Run.

**Source Retrieval Test**
: An administrator-only Knowledge diagnostic that calls one admitted source and
  returns bounded ephemeral evidence. It creates no Agent Conversation, Run,
  message, durable evidence or citation.

**Builder Test Conversation**
: The existing non-market Agent-author test boundary. A draft knowledge test
  uses the normal governed Run path, and every resulting Run, message, evidence
  and citation remains marked as test data.

## 3. Authority map

| Fact | Write authority | Read consumers |
| --- | --- | --- |
| Connection and active revision | Knowledge | Knowledge admin, provider adapter, readiness |
| Knowledge credential reference and resolution authorization | Knowledge | Knowledge provider adapter |
| Credential bytes and physical secret lifecycle | Shared secret infrastructure | Knowledge provider adapter for one admitted call only |
| Logical source and provider binding | Knowledge | Agent Apps, Runs, admin projections |
| Source ACL and authorization version | Knowledge | Agent Apps publication, Run admission, Market projection |
| Agent Profile knowledge selection | Agent Apps | Conversations, Runs, Builder/public projections |
| Retrieval profile and version | Knowledge | Agent Apps publication, Runs, Execution |
| Run Knowledge Snapshot | Runs through Knowledge public API on the Run transaction | Execution, audit |
| Provider request and response | Knowledge infrastructure | Knowledge application only |
| Accepted evidence set | Knowledge | Execution adapter, citation finalization |
| Durable assistant message | Conversations | Chat history, Streaming hydrate |
| Citation snapshot | Knowledge finalized against a Conversations-owned message identity | Chat history, audit, frontend |
| Public event order and transport | Streaming | Frontend reducer |

One context must not write another context's tables. Cross-context finalization
uses narrow public APIs and one application-owned transaction where atomicity is
required.

## 4. Persistence model

All durable tables retain the existing internal single-enterprise deployment
scope for query and foreign-key safety. Product projections omit that internal
scope.

### 4.1 `knowledge_connections`

| Field | Contract |
| --- | --- |
| `id` | Stable platform connection ID |
| `tenant_id` | Internal deployment scope; fixed by server authority |
| `name` | Unique display name within deployment scope |
| `provider_key` | Registered provider key; `ragflow` in v1 |
| `status` | `draft`, `checking`, `cataloging`, `active`, `unavailable`, `disabled` |
| `active_revision_id` | Nullable immutable revision reference |
| `active_catalog_sync_id` | Nullable complete synchronization bound to the active revision |
| `candidate_revision_id` | Nullable sole server-held candidate; never browser-selected during activation |
| `lifecycle_epoch` | Non-negative monotonic epoch incremented by every authority-bearing state or active-pair transition |
| `last_authenticated_check_at` | Nullable timestamp |
| `last_complete_sync_at` | Timestamp of `active_catalog_sync_id`; never a different revision's freshness |
| `safe_failure_code` | Nullable bounded provider-neutral code |
| `created_by`, `created_at`, `updated_at` | Audit identity and timestamps |

Connection names are presentation identities. Provider routing uses the stable
connection ID and immutable revision. The active revision and its complete
catalog synchronization form one atomic authority pair.

`draft`, initial `checking` and initial `cataloging` are pre-authority candidate
states: while no active pair exists, `lifecycle_epoch` remains `0` and no
lifecycle receipt is written. Initial activation writes epoch `1`. Later active-
pair switches, `active <-> unavailable`, disable and recovery each increment the
epoch and write a receipt. Checking or cataloging a replacement candidate while
an active pair continues serving leaves the serving status and epoch unchanged
until the candidate's final switch transaction.

### 4.2 `knowledge_connection_revisions`

| Field | Contract |
| --- | --- |
| `id` | Immutable connection revision ID |
| `connection_id` | Parent connection |
| `revision` | Positive monotonic integer |
| `provider_key` | Registered provider key |
| `base_url` | Canonical provider base URL; never ordinary-user public |
| `secret_ref` | Opaque secret-store reference; never the credential value |
| `transport_policy_json` | Bounded typed timeout, TLS and egress-policy facts |
| `content_hash` | Hash of canonical non-secret definition plus secret revision identity |
| `checked_at` | Nullable authenticated-check time |
| `check_status` | `pending`, `passed`, `failed` |
| `created_by`, `created_at` | Audit identity and timestamp |

A revision is immutable. Credential-reference or transport-policy changes
create another revision. A canonical base-URL or provider-key change identifies
a different provider authority and must create a new Knowledge Connection, so a
provider resource ID can never be reused against another endpoint.

Every authority-bearing connection state or active-pair transition also inserts
one immutable `knowledge_connection_lifecycle_receipt` in the same transaction:

| Field | Contract |
| --- | --- |
| `connection_id`, `lifecycle_epoch` | Unique transition identity |
| `state` | `active`, `unavailable` or `disabled` |
| `active_revision_id`, `active_catalog_sync_id` | Exact authority pair, both null only for `disabled` |
| `operation_id`, `requested_by` | Idempotency and principal binding |
| `created_at` | Audit timestamp; not the concurrency authority |

The connection row lock and monotonic epoch define transition order. Creating a
new candidate atomically replaces `candidate_revision_id`; a stale candidate job
cannot commit unless that pointer and its sync fence still match. A Run
snapshot that stores an epoch can therefore prove which immutable revision and
catalog pair was active at admission even after later transitions.

### 4.3 `knowledge_catalog_syncs`

| Field | Contract |
| --- | --- |
| `id` | Stable synchronization job ID |
| `connection_id`, `connection_revision_id` | Exact catalog authority used |
| `operation_id`, `requested_by` | Caller idempotency and principal binding |
| `retry_of_sync_id` | Nullable prior job linked by an operator retry |
| `purpose` | `manual_active_refresh` or `candidate_activation` |
| `status` | `requested`, `enumerating`, `committing`, `succeeded`, `failed`, `cancelled`, `reconcile_required` |
| `lease_owner`, `lease_generation`, `lease_expires_at` | Monotonic active-job fence |
| `provider_cursor` | Nullable bounded private cursor for the current generation |
| `observed_count`, `page_count` | Bounded progress counters |
| `candidate_digest` | Digest of the complete normalized observation set |
| `safe_failure_code` | Nullable bounded provider-neutral failure |
| `requested_at`, `started_at`, `completed_at` | Lifecycle timestamps |

`(connection_id, operation_id)` is unique. Only the exact active lease
generation may advance the cursor or commit. A lease that expires before a
terminal receipt moves to `reconcile_required`; a successor never treats the
expired generation as a complete catalog.

### 4.4 `knowledge_catalog_sync_observations`

| Field | Contract |
| --- | --- |
| `sync_id`, `lease_generation` | Exact candidate-set fence |
| `provider_resource_id` | Private provider dataset identity |
| `provider_name` | Bounded provider-owned name |
| `provider_metadata_json` | Allowlisted bounded metadata only |
| `record_digest` | Canonical normalized record digest |

The durable candidate set permits crash recovery without modifying the last
complete catalog. A successful commit upserts logical sources, writes the job
receipt and removes candidate rows in one transaction. A failed, cancelled or
reconcile-required generation may have candidate rows removed only after its
terminal receipt is durable; candidate rows are never a public catalog.

### 4.5 `knowledge_sources`

| Field | Contract |
| --- | --- |
| `id` | Stable logical source ID |
| `tenant_id` | Internal deployment scope; fixed by server authority |
| `connection_id` | Owning connection |
| `provider_resource_id` | Private provider dataset ID |
| `provider_name` | Latest bounded provider-owned name |
| `display_name` | Optional administrator-owned alias |
| `description` | Optional bounded safe description |
| `status` | `pending_review`, `active`, `disabled`, `missing` |
| `authorization_version` | Positive monotonic ACL version |
| `provider_metadata_json` | Allowlisted bounded catalog projection |
| `first_seen_at`, `last_seen_at` | Provider catalog observation timestamps |
| `last_complete_sync_id` | Complete synchronization that last observed it |
| `last_seen_connection_revision_id` | Exact checked connection revision that produced the latest observation |
| `created_at`, `updated_at` | Timestamps |

`(tenant_id, connection_id, provider_resource_id)` is unique. Because endpoint
and provider-key changes create a new connection, this identity never crosses
provider authorities. The provider resource ID is encrypted or protected
according to the database threat model and is absent from ordinary-user
projections.

### 4.6 Source ACL records

The source ACL uses one versioned root record and exact child sets for
departments, roles and users. It reuses the same canonical value validation and
department hierarchy semantics as Agent Profile ACL.

| Field | Contract |
| --- | --- |
| `source_id` | Logical source ID |
| `authorization_version` | Positive version bound by Agent publication and Run admission |
| `visibility` | `enterprise` or `restricted` |
| `allowed_department_ids` | Exact validated set |
| `allowed_role_ids` | Exact validated set |
| `allowed_user_ids` | Exact validated set |
| `created_by`, `created_at` | Audit identity and timestamp |

ACL mutation creates a new version. Historical versions remain readable while
an Agent revision, Run snapshot or audit fact references them.

### 4.7 `knowledge_retrieval_profiles`

| Field | v1 contract |
| --- | --- |
| `id` | Stable profile ID |
| `revision` | Positive immutable revision |
| `name` | Administrator-facing name |
| `mode` | `deterministic` in v1; `agent_directed` reserved for the follow-on |
| `top_k_per_source` | Integer `1..20`, default `8` |
| `candidate_pool_size` | Integer `20..4096`, default `1024`, not less than `top_k_per_source` |
| `score_threshold` | Number `0..1`, default `0.45` |
| `fusion_strategy` | `rrf` in v1 |
| `rrf_constant` | Positive integer, default `60` |
| `final_top_k` | Integer `1..20`, default `8` |
| `per_source_timeout_ms` | Integer `100..30000`, default `8000` |
| `overall_timeout_ms` | Integer `100..60000`, default `12000` |
| `cancellation_grace_ms` | Integer `0..2000`, default `250` |
| `max_retries_per_source` | Integer `0..3`, default `1` |
| `retry_backoff_base_ms` | Integer `10..1000`, default `100` |
| `retry_backoff_cap_ms` | Integer `10..5000`, default `1000`, not less than the base |
| `retry_jitter_ratio` | Number `0..0.5`, default `0.2` |
| `max_parallel_sources` | Integer `1..8`, default `4` |
| `max_query_bytes` | Integer, default `16384` |
| `max_chunk_bytes` | Integer, default `16384` |
| `max_total_evidence_bytes` | Integer, default `131072` |
| `status` | `active` or `disabled` |
| `content_hash` | Canonical policy hash |

Changing any policy field creates another immutable revision.

### 4.8 Agent Profile knowledge bindings

The Agent Apps owner persists `knowledge_enabled` on every Agent Profile
Revision. Its create default is `false`; enablement is never inferred from the
source count. A disabled revision may retain source/profile authoring choices,
but its executable `knowledge_bindings` are empty and every runtime ignores
those retained choices. An enabled published revision persists one ordered
binding per logical source and requires a non-empty source set plus one active
retrieval profile.

| Field | Contract |
| --- | --- |
| `agent_id` | Agent identity |
| `profile_revision` | Immutable Agent Profile Revision |
| `source_id` | Logical Knowledge Source ID |
| `source_authorization_version` | ACL version proven at publication |
| `ordinal` | Deterministic zero-based order |
| `required` | `true` in v1 |
| `retrieval_profile_id`, `retrieval_profile_revision` | Exact policy identity |

The ordered binding set participates in the Agent Profile content hash.

### 4.9 `run_knowledge_snapshots`

| Field | Contract |
| --- | --- |
| `run_id` | Unique Run identity and primary binding |
| `agent_id`, `profile_revision`, `profile_content_hash` | Pinned Agent definition |
| `retrieval_profile_id`, `retrieval_profile_revision` | Admitted policy |
| `sources_json` | Ordered bounded logical source, ACL version, connection revision and connection lifecycle epoch tuples |
| `principal_policy_version` | Identity decision version |
| `authorized_at` | Authorization timestamp |
| `content_hash` | Canonical snapshot hash |

The snapshot contains no credential, endpoint, query, chunk text or private
instructions.

### 4.10 `knowledge_retrieval_attempts`

| Field | Contract |
| --- | --- |
| `id` | Retrieval attempt ID |
| `run_id`, `attempt_id`, `generation` | Exact Run attempt fence |
| `snapshot_hash` | Bound Run Knowledge Snapshot |
| `status` | `requested`, `retrieving`, `succeeded`, `no_evidence`, `failed`, `cancelled` |
| `source_count` | Admitted source count |
| `result_count` | Valid provider result count |
| `evidence_count` | Accepted fused evidence count |
| `provider_retry_count` | Bounded total retries performed across admitted sources |
| `duration_ms` | Bounded duration |
| `safe_failure_code` | Nullable provider-neutral error |
| `cancel_requested_at` | Nullable server timestamp persisted under the exact attempt fence |
| `terminal_digest` | Nullable canonical digest of the committed terminal receipt |
| `started_at`, `deadline_at`, `completed_at` | Claim, fixed overall deadline and nullable terminal timestamps |

Raw query text, chunk content and response payloads do not enter this table.
The claim transaction writes `started_at` from the server clock and fixes
`deadline_at = started_at + overall_timeout_ms`. Process-local timeout uses a
monotonic clock capped by the remaining durable deadline. Retries inside the
same attempt keep that deadline; a new Run retry, resume or copy receives a new
attempt deadline only after normal reauthorization.

Here, a Run retry, resume or copy is a user-visible or orchestrator operation
that creates a new Run identity, immutable Run Knowledge Snapshot and attempt;
the Runs authority records any parent lineage. Duplicate dispatch, lease
recovery or worker redelivery of the exact current
`(run_id, attempt_id, generation)` is not such an operation and cannot replace
the snapshot or extend the deadline.

### 4.11 `knowledge_evidence`

Evidence is durable, immutable, attempt-bound Run data required to build Engine
input, survive worker recovery and finalize citations. It is persisted before
Engine dispatch and follows the owning Run's retention contract:

| Field | Contract |
| --- | --- |
| `evidence_id` | Stable platform ID unique within Run |
| `run_id`, `retrieval_attempt_id` | Attempt binding |
| `source_id` | Logical source identity |
| `provider_document_id` | Private stable provider document identity |
| `provider_chunk_id` | Private stable provider chunk identity when supplied |
| `title` | Bounded safe title |
| `content` | Bounded accepted passage |
| `content_sha256` | Digest of accepted passage bytes |
| `provider_score` | Original numeric score |
| `fused_rank` | Deterministic final rank |
| `position_json` | Bounded allowlisted position projection |

Engine input is assembled only from evidence belonging to the exact admitted
Run and successful retrieval attempt.

### 4.12 `knowledge_citations`

| Field | Contract |
| --- | --- |
| `id` | Stable citation ID |
| `message_id`, `run_id` | Durable answer binding |
| `evidence_id` | Same-Run evidence identity |
| `ordinal` | Stable marker order |
| `source_id` | Logical source identity |
| `document_ref` | Provider document identity stored privately |
| `chunk_ref` | Provider chunk identity stored privately when available |
| `title` | Maximum 512 UTF-8 bytes |
| `excerpt` | Maximum 2048 UTF-8 bytes |
| `content_sha256` | Evidence digest |
| `score` | Original provider score |
| `position_json` | Maximum 2048 compact JSON bytes |
| `created_at` | Snapshot time |

At most 20 citations may bind one assistant message. The ordinary-user
projection omits private provider identities.

## 5. Lifecycle state machines

### 5.1 Connection

```text
draft -> checking -> cataloging -> active
          |             |          <-> unavailable
          +-------> unavailable

draft | active | unavailable -> disabled
unavailable | disabled -> active only through guarded candidate activation
```

Only an authenticated passed check followed by a complete synchronization bound
to that exact revision may enter `active`. `disabled` is an administrator
decision. `unavailable` is an observed operational state and may recover
through another checked-and-synchronized revision. When an active, unavailable
or disabled connection has a replacement candidate, check and catalog progress
belong to the candidate revision and synchronization job; the connection keeps
its current serving status and epoch until the guarded final activation commits.

An active connection may hold one checked candidate revision while its prior
`(active_revision_id, active_catalog_sync_id)` pair continues serving Runs whose
Knowledge Snapshot carries the prior lifecycle epoch. Run snapshot admission
and activation, supersession or disable acquire the same connection row lock
until commit. Admission stores the observed `lifecycle_epoch`; a transition
increments it. This serialization, rather than timestamp ordering, proves which
commit won the race. Credential resolution for the pinned revision is limited
to the exact current Run attempt and its existing deadline. A user-visible or
orchestrator retry, resume or copy creates a new Run and reauthorizes against the
current active pair. The candidate may enumerate only inside the candidate-
activation operation after its authenticated check passes. A complete
synchronization bound to that exact candidate revision atomically switches both
active IDs and freshness and increments the lifecycle epoch. Partial, failed,
cancelled, stale or reconcile-required candidate work leaves the prior pair and
epoch unchanged.

### 5.2 Knowledge Source

```text
provider discovery -> pending_review -> active
                           |          <-> disabled
                           +------------> disabled

pending_review | active | disabled -> missing
missing -> pending_review only after a later complete sync observes it
```

Activation and re-enabling require a current valid ACL and active connection.
Only a complete successful catalog synchronization may enter `missing`.
Catalog synchronization never hard-deletes a logical source.

### 5.3 Catalog Synchronization

```text
requested -> enumerating -> committing -> succeeded
                         |            -> failed
requested | enumerating  -> cancelled
requested | enumerating | committing -> reconcile_required
```

Only the exact lease generation may mutate a non-terminal job. Lease expiry or
an unknown commit outcome enters `reconcile_required`; it is never rewritten as
success by elapsed time or an operator guess.

### 5.4 Retrieval Attempt

```text
requested -> retrieving -> succeeded
                        -> no_evidence
                        -> failed
requested | retrieving  -> cancelled
```

Every transition is fenced by exact Run attempt and monotonic generation.
One repository compare-and-set, keyed by exact Run, attempt, generation and
snapshot hash, may change `requested` or `retrieving` to a terminal state. In
that transaction, a persisted `cancel_requested_at <= deadline_at` selects
`cancelled`; otherwise `server_now >= deadline_at` selects `failed` with
`knowledge_retrieval_timeout`; only then may the caller's succeeded,
no-evidence or typed-failure outcome win. A `succeeded` compare-and-set inserts
the complete bounded evidence set and terminal receipt in the same transaction;
every other terminal state inserts zero evidence. The same terminal digest is
idempotent. A different late outcome returns the existing terminal receipt
without mutation; it cannot overwrite the winner or append evidence.

## 6. Provider port

`knowledge.registry` owns the typed provider registry. Bootstrap registers one
production adapter under `ragflow`.

The engine-neutral application port exposes these operations:

```python
class KnowledgeProvider(Protocol):
    async def check_connection(
        self, connection: ProviderConnection
    ) -> ConnectionCheckResult: ...

    async def list_sources(
        self, connection: ProviderConnection, page: ProviderPageRequest
    ) -> ProviderSourcePage: ...

    async def retrieve(
        self,
        connection: ProviderConnection,
        request: ProviderRetrievalRequest,
        control: ProviderCallControl,
    ) -> ProviderRetrievalResult: ...
```

The port does not expose provider SDK objects, HTTP responses, credentials,
provider exceptions or provider-specific record classes.

`ProviderCallControl` carries the remaining monotonic deadline and one
cooperative cancellation signal shared by permit acquisition and transport. A
task waiting for a permit must wake on that signal or deadline without acquiring
one. The adapter binds transport timeout to the same control; cancellation starts
an awaitable abort/close and `retrieve` cannot finish cancellation until the
transport reports closed. The orchestrator owns each acquired permit in a
shielded `finally`-equivalent scope. It stops scheduling retries, signals every
waiter and in-flight call, awaits transport-close acknowledgement and releases
each permit no later than `cancellation_grace_ms`. The shielded cleanup path then
invokes the single terminal compare-and-set even if the surrounding task receives
another cancellation. An adapter that cannot prove close and permit release
inside that grace fails provider-contract acceptance.

Registry invariants:

1. duplicate provider keys fail startup;
2. an unknown configured key fails startup;
3. test doubles are not registered by production bootstrap;
4. the provider key is a stable product protocol constant owned by Knowledge;
5. dynamic Python module or class paths are forbidden configuration values.

## 7. RAGFlow adapter

The v1 provider contract is grounded in the official RAGFlow
[dataset catalog](https://github.com/infiniflow/ragflow/blob/fc62487e39784d35d7450f1552d15daab8e073a7/docs/references/http_api_reference.md#L889-L1040)
and [native retrieval](https://github.com/infiniflow/ragflow/blob/fc62487e39784d35d7450f1552d15daab8e073a7/docs/references/http_api_reference.md#L2793-L2981)
contracts at commit `fc62487e39784d35d7450f1552d15daab8e073a7`.
Before KPRVCAT-18 or KPRVRET-19 starts, the provider-contract fixture must be
checked against the company's deployed RAGFlow version. Any incompatible field,
authentication or identity behavior triggers the slice stop condition rather
than an inferred adapter fallback.

### 7.1 Authentication

The adapter resolves the API key from `secret_ref` immediately before the
request and sends it as:

```http
Authorization: Bearer <api-key>
```

Credential bytes remain inside the infrastructure adapter and secret client.

### 7.2 Authenticated connection check

The canonical v1 check performs a bounded authenticated request against the
dataset catalog:

```http
GET {base_url}/api/v1/datasets?page=1&page_size=1
```

Success requires a valid authenticated response and a typed catalog envelope.
An anonymous health endpoint may contribute liveness diagnostics but cannot
activate the connection.

### 7.3 Source enumeration

The adapter maps provider datasets to `ProviderSourceRecord`:

```text
provider_resource_id  <- dataset.id
provider_name         <- dataset.name
provider_metadata     <- allowlisted bounded catalog fields
```

Provider pagination is translated into an opaque provider cursor owned by the
sync operation. Browser clients never receive that cursor.

### 7.4 Retrieval request

The v1 adapter calls:

```http
POST {base_url}/api/v1/retrieval
Authorization: Bearer <api-key>
Content-Type: application/json
```

The provider request is constructed only from server-owned values:

```json
{
  "question": "<bounded current user text>",
  "dataset_ids": ["<one admitted provider dataset id>"],
  "page": 1,
  "page_size": 8,
  "knn_top_k": 1024,
  "similarity_threshold": 0.45,
  "keyword": false,
  "highlight": false
}
```

`page_size` is the admitted profile's `top_k_per_source` result limit;
`knn_top_k` is its separate `candidate_pool_size`. The adapter never substitutes
the deprecated RAGFlow `top_k` alias for either value.

One v1 provider call contains exactly one logical source. This permits bounded
parallel fan-out and avoids assuming scores or embedding models are comparable
across provider datasets.

### 7.5 Retrieval response

The adapter accepts only a valid success envelope with a chunk list. Each chunk
is normalized from these provider facts when present:

```text
id                  -> provider_chunk_id
content             -> content
dataset_id          -> provider_dataset_id validation
document_id         -> provider_document_id
document_keyword    -> title
similarity          -> provider_score
positions           -> position_json
```

The adapter rejects a chunk when:

- `content` is absent or not a string;
- `document_id` is absent or empty;
- `id` is absent when the native endpoint contract supplies chunk identity;
- `similarity` is not a finite number;
- `dataset_id` does not match the requested source; or
- the bounded projection cannot be constructed safely.

## 8. Application APIs

`knowledge.api` exposes narrow typed operations to peer contexts:

| Operation | Caller | Result |
| --- | --- | --- |
| `CreateConnection` | Admin transport | Draft connection projection |
| `CheckConnection` | Admin transport | Checked revision projection |
| `UpdateConnection` | Admin transport | New draft revision |
| `ActivateConnectionCandidate` | Admin transport | Idempotent candidate-activation job identity |
| `DisableConnection` | Admin transport | Disabled connection projection |
| `StartCatalogSync` | Admin transport, scheduler | Idempotent sync job identity |
| `GetCatalogSync` | Admin transport | Safe job projection |
| `ListKnowledgeSources` | Admin transport, Agent Apps | Authorized bounded projections |
| `UpdateSourcePresentation` | Admin transport | Updated source projection |
| `UpdateSourceStatus` | Admin transport | Status-transition receipt |
| `ReplaceSourceAcl` | Admin transport | New authorization version |
| `RunSourceRetrievalTest` | Admin transport | Ephemeral bounded test result |
| `AuthorizeSourceSetForPublication` | Agent Apps | Exact authorized source/version receipt |
| `AuthorizeSourceSetForRun` | Runs | Exact Run-admission source receipt |
| `ResolveRunKnowledgeSnapshot` | Runs, Execution | Immutable snapshot projection |
| `RetrieveEvidence` | Execution | Bounded evidence set or typed outcome |
| `FinalizeCitations` | Conversations/Execution orchestration | Durable citation receipt |
| `ListMessageCitations` | Conversations history transport | Safe authorized citation projections |

Peer contexts call `knowledge.api`; they do not import Knowledge application,
domain, infrastructure or transport modules.

### 8.1 Command authorization and side effects

| Operation | Required authority | Durable side effects |
| --- | --- | --- |
| Create, update, check, activate candidate or disable connection | Platform connection administrator | Connection revision/state, activation job, redacted audit and idempotency receipt |
| List or inspect administrative connections | Platform connection administrator or knowledge administrator | None |
| Start or retry catalog synchronization | Knowledge administrator or server-owned scheduler principal | Synchronization job, candidate observations, catalog receipt and redacted audit |
| Update source presentation or status | Knowledge administrator | Source revision/state and redacted audit |
| Replace source ACL | Knowledge administrator | New immutable authorization version and redacted audit |
| Run source retrieval test | Knowledge administrator authorized for the source | Redacted audit metadata only; no Conversation, Run, message, durable evidence or citation |
| List sources for Agent Builder | Agent Apps server operation for the current author | None |
| Authorize publication or Run | Agent Apps or Runs server operation | Versioned receipt or Run Knowledge Snapshot through its owning transaction |
| Retrieve and finalize evidence | Execution or Conversations orchestration for the exact Run | Attempt, evidence and citation facts only |

Role claims, department facts and user grants are loaded from server-owned
identity authorities. A browser-supplied role or department never satisfies
this table. A denied administrative target is projected as not found unless an
authorized caller already has catalog visibility.

## 9. HTTP surfaces

Transport paths may be aligned with the current router assembly, but the
following semantic operations are stable.

### 9.1 Administrative connection routes

```text
POST   /admin/knowledge/connections
GET    /admin/knowledge/connections
GET    /admin/knowledge/connections/{connection_id}
PATCH  /admin/knowledge/connections/{connection_id}
POST   /admin/knowledge/connections/{connection_id}/check
POST   /admin/knowledge/connections/{connection_id}/activate-candidate
POST   /admin/knowledge/connections/{connection_id}/disable
```

Mutation requests require a caller-owned operation ID. Read responses expose a
safe URL origin only to authorized administrators and never expose credentials.
PATCH accepts credential-reference and transport-policy changes only and
creates the sole candidate immutable revision. A canonical base-URL or provider-
key change is rejected and requires POST to create another connection.
`activate-candidate` requires a caller operation ID and is the only command that
may start a `candidate_activation` synchronization. It server-resolves the sole
candidate, performs a fresh authenticated check, enumerates its complete catalog
and returns the same activation-job identity for an idempotent repeat. Only the
fenced job's final transaction may switch the active revision/catalog pair and
lifecycle epoch; the browser cannot nominate a revision or sync receipt.

### 9.2 Administrative source routes

```text
POST   /admin/knowledge/connections/{connection_id}/syncs
GET    /admin/knowledge/syncs/{sync_id}
GET    /admin/knowledge/sources
GET    /admin/knowledge/sources/{source_id}
PATCH  /admin/knowledge/sources/{source_id}
PUT    /admin/knowledge/sources/{source_id}/acl
POST   /admin/knowledge/sources/{source_id}/retrieval-tests
```

List routes use opaque cursors and hard limits. Retrieval-test responses use
the same normalization and payload bounds as ordinary retrieval. The response
contains ephemeral evidence IDs, safe titles, bounded excerpts, scores and
positions only. Completion or abort releases the working set; the operation
creates no Conversation, Run, message, durable evidence, citation or ordinary
history item. Its audit fact contains logical IDs, result count, duration and a
safe outcome, never the query or chunk content.

### 9.3 Agent Profile routes

Existing Agent Profile draft and publish APIs gain these private definition
fields:

```json
{
  "knowledge_enabled": true,
  "knowledge_source_ids": ["ks_..."],
  "retrieval_profile_id": "krp_..."
}
```

Public Agent projections expose only:

```json
{
  "knowledge_capability": {
    "enabled": true,
    "source_count": 2,
    "freshness_at": "<bounded source-catalog time or null>"
  }
}
```

They do not expose logical source IDs, provider identities, raw ACLs or
retrieval policy internals.

`knowledge_capability.enabled` projects the immutable revision flag rather
than `source_count > 0`. A disabled revision reports `source_count: 0` and
`freshness_at: null`, including when its administrative draft configuration
retains selections for later re-enablement.

An Agent Builder draft test is different from the administrative source test.
It uses the existing Builder Test Conversation and the normal governed Run
path so the author can verify the whole draft. The test definition, Run,
evidence, message and citations carry `builder_test`; Market and ordinary-user
history queries exclude that scope.

### 9.4 Chat history

The existing bounded canonical route:

```text
GET /chat/sessions/{session_id}/messages
```

adds an optional `citations` array to each authorized assistant message. The
array is read from durable citation snapshots and is ordered by `ordinal`.
Existing clients may ignore the additive field.

## 10. Retrieval orchestration

### 10.1 Admission

Run admission performs these steps in order:

1. restore the pinned Agent Profile Revision;
2. reauthorize the principal against Agent publication and ACL;
3. read the immutable `knowledge_enabled` flag; when it is `false`, skip every
   Knowledge snapshot/provider operation and continue through the ordinary
   non-Knowledge Engine path;
4. for an enabled revision, load its immutable non-empty source bindings;
5. reauthorize each required source against current principal facts;
6. begin the Run/snapshot transaction, lock every distinct source row by
   ascending `source_id`, then every distinct connection row by ascending
   `connection_id`;
7. under those locks, re-read and reauthorize source status/ACL and connection
   status, active revision/catalog pair and lifecycle epoch;
8. resolve the exact active connection revisions and verify the immutable
   retrieval profile revision;
9. persist the Run Knowledge Snapshot in that transaction; and
10. dispatch only after the snapshot commit succeeds.

The source-then-connection type order and ascending IDs are global. Callers may
not lock in binding order. Any changed or uncertain fact before step 10 rolls
back the snapshot and produces zero provider and Engine calls.

Every source-status or active-ACL-pointer mutation acquires its source row;
every authority-bearing connection transition acquires its connection row.
Those writers therefore serialize with the admission locks above.

### 10.2 Provider fan-out

The worker claims one retrieval attempt under the exact Run attempt generation.
It queries one logical source per provider request. At most
`max_parallel_sources` requests run concurrently. The overall deadline includes
all provider calls, validation and fusion. Cancellation must release remaining
work no later than the typed `cancellation_grace_ms` after that fixed deadline.

For retry number `i`, starting at one, the unjittered delay is
`min(retry_backoff_cap_ms, retry_backoff_base_ms * 2^(i-1))`. The applied delay
is sampled between `delay * (1 - retry_jitter_ratio)` and
`min(retry_backoff_cap_ms, delay * (1 + retry_jitter_ratio))`. An injected clock
and random source make this policy testable. A source receives no more than
`max_retries_per_source` retries. Cancellable backoff is capped to the remaining
overall deadline; reaching zero remaining time signals cancellation and selects
the timeout terminal path without another provider call. Provider-call timeout
is capped by both the per-source timeout and remaining overall deadline.

Retryability is one centralized function over typed provider outcomes:

| Typed outcome | Retry decision |
| --- | --- |
| `knowledge_provider_transient` | Retry within count and deadline for connect reset, per-call timeout, HTTP 429, 502, 503 or 504 |
| `knowledge_provider_rejected` | Never retry authentication or authorization rejection |
| `knowledge_response_invalid` | Never retry a typed-contract violation |
| `knowledge_connection_invalid` | Never retry TLS, egress or configuration rejection inside a Run |
| `knowledge_source_missing`, `knowledge_access_denied`, `knowledge_binding_invalid`, `knowledge_profile_invalid` | Never retry |
| Any unknown or unmapped outcome | Never retry; fail closed as a safe provider error |

No adapter or route carries a separate retryability list.

A required source failure fails the attempt. The first release does not silently
omit one failed required source from an otherwise successful answer.

### 10.3 Fusion

Each valid source result is ordered by provider rank after applying its score
threshold. Deduplication uses:

```text
(provider_key, source_id, provider_document_id, provider_chunk_id)
```

The first release uses reciprocal-rank fusion:

```text
rrf_score(item) = sum(1 / (rrf_constant + rank_in_source))
```

Final order is:

1. descending RRF score;
2. ascending source ordinal from the Agent revision;
3. ascending provider document identity;
4. ascending provider chunk identity.

Final evidence is capped by `final_top_k` and the total evidence byte budget.

### 10.4 Engine adaptation

Execution receives engine-neutral evidence. The Claude adapter renders a
structured evidence section that:

- identifies each passage only by platform evidence ID and safe title;
- clearly separates evidence from system instructions and user text;
- requires citations to use the supplied evidence IDs;
- does not include provider URL, credential, dataset ID or ACL; and
- does not grant a new Skill or MCP capability.

### 10.5 No-evidence outcome

A valid retrieval with zero accepted chunks records `no_evidence`. The first
release returns the deterministic ordinary-user message:

> 未在当前已授权知识库中找到可支持回答的内容。请补充关键词或换一种问法。

No Engine dispatch is required for this outcome.

## 11. Citation finalization

The finalization operation accepts:

```text
run_id
assistant_message_id
ordered cited evidence IDs
retrieval attempt generation
caller operation identity
```

It verifies:

1. the message belongs to the same Run;
2. the retrieval attempt succeeded for the exact Run generation;
3. every evidence ID belongs to that attempt;
4. no evidence ID is repeated;
5. the final count is at most 20; and
6. every field fits its persisted bound.

Message completion and citation finalization require an atomic orchestration
contract. Either the message and citations commit together, or the message
remains incomplete and terminal hydrate cannot claim citation readiness.

## 12. Public projection and SSE

The first release does not add a second stream or provider-specific SSE frame.
It uses the existing v4 terminal and hydrate sequence:

1. the durable assistant message and citation snapshots commit;
2. the Runs owner commits the truthful terminal event;
3. Streaming projects the existing `run.succeeded` event with
   `hydrate_required=true`;
4. the frontend performs authorized durable hydrate; and
5. the message reducer replaces provisional state with the message and ordered
   citations exactly once.

Citation markers therefore survive reconnect, replay gaps and page refresh
through the existing terminal hydrate authority. A later live citation event
requires a separately versioned public wire decision and release-atomic SSE
change.

## 13. Error taxonomy

Knowledge owns typed internal errors. HTTP, terminal and ordinary-user wording
are safe projections.

| Stable code | Meaning | Ordinary-user outcome |
| --- | --- | --- |
| `knowledge_connection_invalid` | Connection definition cannot be used | Administrator configuration required |
| `knowledge_connection_unavailable` | Bounded provider call could not complete | Knowledge service temporarily unavailable |
| `knowledge_source_missing` | Bound provider dataset is absent | Agent requires administrator attention |
| `knowledge_source_disabled` | Source was administratively disabled | Agent requires administrator attention |
| `knowledge_access_denied` | Current principal lacks source access | Request denied without source disclosure |
| `knowledge_binding_invalid` | Agent/source/revision binding is inconsistent | Agent requires administrator attention |
| `knowledge_profile_invalid` | Retrieval profile is unavailable or invalid | Agent requires administrator attention |
| `knowledge_retrieval_timeout` | Retrieval exceeded its deadline | Retry later |
| `knowledge_provider_transient` | Retryable connect reset, per-call timeout or HTTP 429/502/503/504 | Knowledge service temporarily unavailable |
| `knowledge_provider_rejected` | Provider rejected authenticated request | Knowledge service unavailable |
| `knowledge_response_invalid` | Provider response violated the typed contract | Knowledge service unavailable |
| `knowledge_no_evidence` | Valid retrieval yielded no accepted evidence | Reformulate the question |
| `knowledge_citation_invalid` | Final answer references invalid evidence | Run fails before grounded success |

Raw upstream codes and messages are stored only in bounded protected operator
diagnostics when policy permits. They are never ordinary-user detail.

## 14. Idempotency and concurrency

### 14.1 Administrative mutations

- Create, update, check, activate-candidate, disable, sync and ACL replacement
  use caller operation identities keyed by principal, operation kind, target (or
  create scope) and operation ID, with a canonical input fingerprint.
- Reusing an operation identity with a different canonical input fingerprint
  fails closed.
- One connection check may supersede an older check only through monotonic
  revision identity.

### 14.2 Catalog synchronization

- One connection has at most one active enumeration lease.
- A manual synchronization enumerates the active revision only.
- Candidate-revision enumeration is internal to the connection-activation
  operation and begins only after the candidate's authenticated check passes.
- A synchronization builds a candidate observation set outside the catalog
  replacement transaction.
- The final catalog/activation transaction resolves all existing affected source
  rows, locks them by ascending `source_id`, then locks the connection row. It
  rechecks the sync lease, candidate pointer and lifecycle epoch after locking;
  new logical source rows rely on the exact uniqueness constraint. No writer may
  acquire connection then source in the same transaction.
- Only a complete candidate may atomically update `last_complete_sync_at` and
  mark absent sources `missing`.
- A candidate revision becomes active only when the same commit binds
  `active_revision_id`, `active_catalog_sync_id` and connection freshness.
- Failure or partial completion preserves the prior active revision, catalog
  generation and freshness.
- An expired synchronization lease cannot commit after a successor lease.

### 14.3 Run retrieval

- One exact `(run_id, attempt_id, generation, snapshot_hash)` owns retrieval.
- Multi-source admission locks all distinct source rows and then connection rows,
  each by ascending stable ID, and re-reads every admission fact after locking.
- Snapshot admission and connection activation, supersession or disable
  serialize on the connection row. The snapshot stores the observed lifecycle
  epoch and the transition increments it, so the committed winner is proven
  without comparing wall-clock timestamps.
- A connection disable or revision switch does not widen snapshot authority:
  only the exact current attempt whose snapshot epoch has an immutable lifecycle
  receipt binding the pinned revision may resolve its credential, and only
  before the existing deadline. User-visible or orchestrator retry, resume and
  copy operations create a new Run identity and reauthorize against the current
  active revision.
- `deadline_at` is fixed when the attempt is claimed. An in-attempt provider
  retry cannot extend it; a worker uses monotonic elapsed time capped by the
  remaining durable deadline and releases work within `cancellation_grace_ms`.
- Only `knowledge_provider_transient` may retry, using cancellable permit wait
  and backoff capped by the remaining overall deadline; every other typed or
  unknown outcome fails without retry.
- Provider work and permits are released before the shielded terminal compare-
  and-set. Persisted cancellation at or before `deadline_at` wins `cancelled`;
  deadline exhaustion first wins `failed/knowledge_retrieval_timeout`; any later
  conflicting terminal writer observes the existing receipt and performs no
  mutation.
- A stale generation cannot persist evidence or finalize citations.
- Duplicate provider responses deduplicate before persistence.
- Duplicate finalization with the same message and ordered evidence set returns
  the existing receipt.
- Duplicate finalization with a different set fails closed.

## 15. Security and redaction

1. Provider egress is server-side and policy allowlisted.
2. Redirects to another origin are rejected unless explicitly allowed by the
   connection transport policy.
3. TLS verification is enabled by default.
4. Credential values are write-only and secret-backed.
5. Provider resource IDs remain private admin/runtime data.
6. Query text, chunk text and raw provider bodies are excluded from ordinary
   logs, metrics and audit payloads.
7. Citation excerpts are bounded durable business data and inherit conversation
   read authorization.
8. Ordinary-user error details do not reveal connection, source, ACL or provider
   identity.
9. Provider content is untrusted data. It cannot alter platform authority,
   system instructions, Skill admission, tool permission or citation identity.
10. Metadata filters become available only through a future allowlisted typed
    policy; arbitrary provider expressions are not accepted from clients or
    models.

## 16. Data and payload bounds

| Value | v1 maximum |
| --- | ---: |
| Bound knowledge sources per Agent revision | 8 |
| Provider page size | 100 records |
| Retrieval query | 16 KiB UTF-8 |
| Provider results accepted per source | 20 |
| One evidence content | 16 KiB UTF-8 |
| Total Engine evidence content | 128 KiB UTF-8 |
| Evidence metadata | 8 KiB compact JSON |
| Citations per assistant message | 20 |
| Citation title | 512 bytes UTF-8 |
| Citation excerpt | 2 KiB UTF-8 |
| Citation position | 2 KiB compact JSON |
| Connection safe failure detail | 1 KiB UTF-8 |
| Catalog synchronization lease | 120 seconds by default; typed and bounded |
| Retrieval cancellation grace | 250 milliseconds by default; typed `0..2000` |

All bounds are enforced before persistence or Engine dispatch. Raising a bound
requires a reviewed context, PostgreSQL and frontend rendering impact decision.

## 17. Retention and deletion

Knowledge adopts the platform's fail-safe durable-data default: retention days
is `0`, meaning retain and do not physically delete. The first release rejects
a non-zero Knowledge retention setting because no reference-safe cleaner is
authorized yet.

The lifecycle rules are:

1. accepted evidence is durable Run-owned data and is immutable after a
   retrieval attempt succeeds;
2. evidence follows the owning Run retention decision, while citation snapshots
   follow the owning assistant message retention decision;
3. connection revisions and source ACL versions remain readable while an Agent
   revision, Run snapshot or audit fact references them;
4. synchronization receipts, retrieval-attempt metadata and Knowledge audit
   facts use the same retain-by-default behavior;
5. synchronization candidate rows are working data and are removed only after
   a durable terminal synchronization receipt, as defined in section 4.4;
6. source-retrieval-test evidence is ephemeral and is released at response
   completion or abort;
7. no v1 Knowledge route physically deletes a connection, source, evidence or
   citation; and
8. a future Conversation or Run deletion workflow must decide related evidence
   and citation disposition through a reviewed cross-context contract rather
   than a Knowledge-only cascade.

Knowledge stores only `secret_ref`. A disabled or superseded revision may be
resolved only for the exact current attempt whose Run Knowledge Snapshot epoch
has an immutable lifecycle receipt binding that revision, and only until the
fixed attempt deadline. User-visible or orchestrator retry, resume and copy
operations create a new Run identity and cannot inherit that exception.
Physical secret destruction is an explicit secret-authority operation and is
never inferred from a Knowledge-row deletion or retention decision.

## 18. Operator alerts and recovery

Alert evaluation uses typed settings; the v1 defaults are:

| Alert | Default trigger | Clear condition |
| --- | --- | --- |
| `knowledge_connection_auth_invalid` | One authenticated check of an active connection reports missing/rejected credential | A later authenticated check passes |
| `knowledge_sync_reconcile_required` | One synchronization lease expires or commit outcome is unknown | A successor synchronization succeeds or an operator records an explicit disposition |
| `knowledge_retrieval_failure_ratio` | Failure ratio at least 20% over 5 minutes with at least 20 attempts | Ratio remains below threshold for one complete window |
| `knowledge_retrieval_timeout_ratio` | Timeout ratio at least 10% over 5 minutes with at least 20 attempts | Ratio remains below threshold for one complete window |
| `knowledge_provider_permit_saturation` | Permit-wait p95 exceeds 1000 ms over 5 minutes with at least 20 waits | p95 remains below threshold for one complete window |

Operator projections expose logical connection/source IDs, safe failure class,
job state, counts, durations and correlation IDs only. They never contain API
keys, base URLs, queries, chunks or provider response bodies.

Recovery actions are limited to authenticated connection check, source or
connection disable, synchronization retry through the same application command
with a new operation identity linked to the prior job, and explicit alert
disposition. Each action is audited. Direct database edits and hand-authored
provider calls are not recovery procedures.

## 19. Rollback and compatibility

- Schema changes are additive and use the canonical migration runner.
- The initial single-server rollout is a drained upgrade: stop the prior API and
  Worker binaries before applying the `knowledge_enabled` backfill and published
  binding constraint, then start only the matching application version. Mixed
  old/new writers are not a supported migration state for this schema step.
- Application rollback may stop offering new Knowledge functionality while
  retaining connections, source bindings, Run snapshots and citations.
- Rollback must not delete immutable Agent revisions or rewrite conversations.
- Existing clients ignore additive private Agent draft fields and message
  citation projections.
- Existing published Agents with no knowledge binding continue unchanged.
- Historical RAGFlow-specific seeded identities require the deletion proof and
  persisted-consumer inventory in the implementation contract before removal.
