# Agent Profile Application and Persistence Boundary

Status: normative source-architecture decision

Owner: `agent_apps` bounded context

Parent contract: [`source-code-architecture.md`](source-code-architecture.md)

Product authority: [GitHub Issue #701](https://github.com/demonsxxxxxx/ai-platform/issues/701)

Decision issue: [GitHub Issue #1039](https://github.com/demonsxxxxxx/ai-platform/issues/1039)

## 1. Decision

Agent Profile revision, publication, visibility, and admission are one Agent Apps
application capability. They are not a generic repository facility, a route
concern, a Skill or MCP lifecycle, a Conversation write authority, or an import
compatibility feature.

The target follows a domain-first modular-monolith and ports-and-adapters shape:

```text
Agent Apps HTTP transport ─┐
Chat / Runs application ───┼─> AgentProfileService / AgentProfileAuthority
Worker application ────────┘                │
                                            ├─> AgentProfileUnitOfWork
                                            │     ├─> AgentProfileRepository
                                            │     └─> AuditLedgerWriter
                                            ├─> SkillAuthorityClient
                                            ├─> McpAuthorityClient
                                            ├─> PrincipalDirectory
                                            ├─> ConversationClient
                                            └─> RunReadClient

bootstrap.api / bootstrap.worker construct and inject concrete adapters.
PostgresAgentProfileRepository implements AgentProfileRepository.
```

`AgentProfileAuthority` remains the single application policy owner. It receives
its ports explicitly and does not import `app.repositories`, a route, bootstrap,
or a concrete PostgreSQL adapter. Peer contexts call the narrow contract exposed
by `app.agent_apps.api`; they do not import Agent Apps domain, application, or
infrastructure internals.

### 1.1 Decision-baseline gaps

This document defines the target and migration evidence. It does not claim that
the decision baseline already implements the boundary. At baseline
`49a37b890028eafd822f68c0bdeaa97253b83248`:

- Agent Profile SQL and transaction-scoped advisory locks have one canonical
  implementation in `app.agent_apps.infrastructure.postgres`;
- `app.repositories` preserves thirteen exact identity aliases to that adapter;
- `AgentProfileAuthority` still imports `app.repositories` and accepts a raw
  psycopg connection on its public methods;
- the compatibility module `app.agent_profiles` and the Agent Profile transport
  each construct a module-global zero-argument authority instance;
- Chat, Runs, and Worker call module-level compatibility functions rather than an
  explicitly injected Agent Apps application service;
- `AgentProfileService`, `AgentProfileUnitOfWork`,
  `AgentProfileRepository`, and `PostgresAgentProfileRepository` do not yet
  exist as typed contracts/adapters;
- `app.bootstrap` is not yet the API/worker composition root; and
- capability, principal, Conversation, Run, and audit dependencies remain mixed
  behind the global repository facade; and
- the frozen global file-authorization bridge still accepts dormant
  `agent_profile_supported_input_types` and
  `agent_profile_supported_file_types` parameters, although supported production
  callers no longer pass them. Deleting those parameters belongs to the governed
  bridge-retirement slice; they are not a supported Agent Profile policy surface.

Those are migration gaps, not precedent. The existing identity aliases do not
make `app.repositories` an Agent Profile owner or a public cross-domain API.

## 2. Source ownership

| Concern | Canonical owner | Allowed contents | Forbidden contents |
| --- | --- | --- | --- |
| Profile definition and lifecycle rules | `app.agent_apps.domain` | immutable definition values, ACL/visibility policy, revision/hash decisions, typed domain errors | psycopg, FastAPI, route DTOs, repository calls, Skill/MCP implementation imports |
| Profile commands and queries | `app.agent_apps.application` | `AgentProfileAuthority`, commands/queries/results, application ports, Unit-of-Work orchestration | concrete adapters, process globals, `ContextVar`, service locators, route imports |
| Public in-process contract | `app.agent_apps.api` | smallest stable service protocol, commands/results/projections and public Agent Apps value types | concrete infrastructure, adapter construction, SQL, compatibility behavior |
| PostgreSQL persistence | `app.agent_apps.infrastructure.postgres` | repository adapter, SQL, row/advisory locks, optimistic fences, record mapping | lifecycle policy, public wording, HTTP mapping, independent transaction/commit |
| API/worker composition | `app.bootstrap.api`, `app.bootstrap.worker` | construct the complete service graph once and inject typed dependencies | business decisions, SQL, request handling, fallback construction |
| HTTP transport | `app.agent_apps.transport.http` | authentication/input translation, service invocation, safe response/error mapping | SQL, concrete adapters, cross-context repository calls, bootstrap imports |
| Legacy import surface | temporary `app.agent_profiles` and governed `app.repositories` aliases | logic-free delegation for inventoried consumers during migration | adapter construction, authorization, lifecycle branching, SQL, indefinite aliases |

The `agent_apps` context owns `agent_profiles`, immutable
`agent_profile_revisions`, Agent Profile publication state, ACL/visibility
decisions, and the private admitted definition derived from one exact published
revision. It does not own Skill release, MCP catalog state, Conversation history,
Run lifecycle, principal identity, or a generic audit database.

## 3. Application contracts

### 3.1 Public service

`app.agent_apps.api` exposes the following asynchronous
`AgentProfileService` operations. Separate use-case objects are allowed only
when their arguments, results, errors, and transaction semantics are identical
to this table; a generic `execute(name, payload)` boundary is forbidden.

| Operation | Normative signature | Result and identity rule | Application errors |
| --- | --- | --- | --- |
| Save draft | `save_draft(scope: AgentProfileScope, command: SaveDraftCommand) -> AgentProfileMutation` | Returns the persisted admin projection, durable audit identity, and exact `(agent_id, revision, content_hash)` binding. `expected_previous_revision` is mandatory for an existing profile. | `ProfileForbidden`, `ProfileDefinitionInvalid`, `ProfileRevisionConflict`, `ProfileDependencyUnavailable` |
| Validate draft | `validate_draft(scope: AgentProfileScope, binding: ProfileBinding) -> ProfileValidationResult` | Validates exactly the supplied revision/hash; it never substitutes the latest draft. | `ProfileForbidden`, `ProfileNotFound`, `ProfileBindingConflict`, `ProfileDependencyUnavailable` |
| Publish draft | `publish_draft(scope: AgentProfileScope, binding: ProfileBinding) -> AgentProfileMutation` | Publishes exactly the validated revision/hash and returns the resulting exact binding and audit identity. | `ProfileForbidden`, `ProfileNotFound`, `ProfileBindingConflict`, `ProfileDependencyUnavailable` |
| Withdraw publication | `withdraw_publication(scope: AgentProfileScope, binding: ProfileBinding) -> AgentProfileMutation` | Withdraws only the currently published exact binding. | `ProfileForbidden`, `ProfileNotFound`, `ProfileBindingConflict` |
| List public profiles | `list_public_profiles(scope: AgentProfileScope, query: PublicProfileQuery) -> tuple[PublishedProfileProjection, ...]` | Applies current tenant, workspace, user, role, department, visibility, and publication rules before constructing the public allowlist projection. | `ProfileForbidden` |
| Get public profile | `get_public_profile(scope: AgentProfileScope, agent_id: str) -> PublishedProfileProjection` | Returns the current authorized publication only; an unauthorized profile is not distinguishable from an absent one. | `ProfileNotFound` |
| List admin revisions | `list_admin_profiles(scope: AgentProfileScope, query: AdminProfileQuery) -> tuple[AdminProfileProjection, ...]` | Requires `scope.is_ai_admin`; returns only same-tenant records. | `ProfileForbidden` |
| Get admin revision | `get_admin_revision(scope: AgentProfileScope, selector: RevisionSelector) -> AdminProfileProjection` | Requires `scope.is_ai_admin` and the exact revision selector. | `ProfileForbidden`, `ProfileNotFound` |
| Resolve new admission | `resolve_for_admission(scope: AgentProfileScope, agent_id: str) -> AdmittedProfileDefinition` | Resolves current publication and returns one exact binding plus private execution definition after current ACL, Skill, and MCP authorization. | `ProfileNotFound`, `ProfileForbidden`, `ProfileDependencyUnavailable` |
| Reauthorize persisted binding | `reauthorize_binding(scope: AgentProfileScope, command: ReauthorizeProfileBinding) -> AdmittedProfileDefinition` | Reauthorizes `command.binding` and typed Run/Session replay facts without advancing revision or hash. | `ProfileNotFound`, `ProfileForbidden`, `ProfileBindingConflict`, `ProfileDependencyUnavailable` |
| Create Agent Conversation | `create_agent_conversation(scope: AgentProfileScope, command: CreateAgentConversation) -> ConversationReference` | Uses one authorized exact profile binding and delegates the Conversation write through the transaction-bound Conversation port. | `ProfileNotFound`, `ProfileForbidden`, `ProfileBindingConflict`, `ProfileDependencyUnavailable` |

The application-owned value contract is also normative:

| Type | Required fields and constraints |
| --- | --- |
| `AgentProfileScope` | Non-empty `tenant_id`, `workspace_id`, and `user_id`; `is_ai_admin`; immutable department and role sets used for authorization. No request headers, bearer tokens, or database principal rows. |
| `ProfileBinding` | `agent_id`, positive `revision`, and canonical lowercase SHA-256 `content_hash`. The three fields are inseparable wherever an existing revision is selected. |
| `SaveDraftCommand` | `agent_id`, `expected_previous_revision`, typed immutable profile definition, and no actor/scope fields duplicated from `AgentProfileScope`. The authority canonicalizes the definition and computes its hash before persistence. |
| `RevisionSelector` | `agent_id`, positive `revision`, and optional lifecycle-status constraint; tenant identity comes only from `AgentProfileScope`. |
| `AgentProfileMutation` | Exact `ProfileBinding`, `AdminProfileProjection`, and durable `audit_id`; never a PostgreSQL row or transport response. |
| `AdmittedProfileDefinition` | Exact `ProfileBinding`, private execution definition, authorized Skill release/material binding, and authorized MCP selection. It is never serialized into an ordinary-user response. |
| `ReauthorizeProfileBinding` | Persisted `ProfileBinding` plus typed immutable Run/Session facts needed to prove replay authority; it contains no raw run row or queue payload. |

All command methods require an `AgentProfileScope`. Tenant, workspace, and user
scope may not be supplied as unrelated primitive keyword arguments, inferred
from rows, or defaulted to `"default"` inside the service. Query/list inputs are
bounded typed values and reject unknown fields.

The service contract does not expose PostgreSQL records, `AsyncConnection`,
repository functions, private infrastructure types, or every method on the
concrete authority class. HTTP status and wire DTOs remain transport concerns.

#### 3.1.1 Public and administrator projection contract

`PublishedProfileProjection` is an ordinary-user allowlist. It contains exactly:

`agent_id`, `expected_revision`, `name`, `description`, `welcome_message`,
`starter_prompts`, `capability_summary`, `recommended_tasks`,
`supported_input_types`, `expected_outputs`,
`permissions_and_data_access_notice`, `avatar_ref`, `avatar_seed`, `category`,
and `published_at`.

Unknown fields are forbidden. Construction must call the Agent Apps public
projection policy; serializing a persistence record, admitted private
definition, admin projection, or generic `dict` directly is forbidden. The same
safe field semantics apply to catalog cards and the embedded Agent Profile
identity in ordinary-user Conversation recovery. Conversation recovery maps
`expected_revision` to its immutable `revision` field; all other public fields
retain the same meaning and forbidden-field rules.

The ordinary-user projection must not contain, including in nested maps/lists:

- instructions, system prompts, private definition payloads, or executor-private
  payloads;
- `model_id`, raw `skill_id`, `skill_version`, `skill_set`, `selected_skill`,
  MCP tool identifiers, release/material hashes, or command fingerprints;
- `content_hash`, storage keys, `avatar_asset_id`, runtime/container paths,
  internal file/artifact identifiers, or queue/executor configuration;
- tenant identifiers, ACL member/department/role lists, audit identifiers,
  creator/publisher identifiers, or internal lifecycle timestamps other than
  the allowlisted public `published_at`; or
- secrets, credentials, tokens, secret-like key/value pairs, or values rejected
  by the shared projection-redaction contract.

`AdminProfileProjection` is a separate admin-only allowlist with exactly:
`agent_id`, `revision`, `published_revision`, `status`, `name`, `description`,
`welcome_message`,
`starter_prompts`, `capability_summary`, `recommended_tasks`,
`supported_input_types`, `expected_outputs`,
`permissions_and_data_access_notice`, `instructions`, `model_id`, `skill_set`,
`selected_skill`, `mcp_tool_ids`, `avatar_ref`, `avatar_asset_id`, `avatar_seed`,
`category`, `visibility`, `allowed_department_ids`, `allowed_roles`,
`allowed_user_ids`, `content_hash`, `created_at`, and `published_at`. It remains
gated by same-tenant `is_ai_admin` authorization; ordinary-user code must never
obtain this type and admin authorization does not permit storage keys, runtime
paths, executor-private payloads, or secrets.

Both projection models are closed typed records (`extra="forbid"`). Adding,
renaming, or exposing a field is a product/security behavior change, not a
source replay.

`supported_input_types` is the universal `["text", "file"]` capability. It is
not an administrator-configurable per-Profile restriction. `supported_file_types`
is not an application command, definition, or projection field. A physical
legacy column may remain temporarily inside the PostgreSQL adapter only to
verify immutable historical hashes and preserve rollback compatibility; no
application, transport, or executor path may use it as Agent admission policy.

### 3.2 Persistence port

`AgentProfileRepository` is an application-owned capability noun. Its async
protocol contains exactly the operations below. The legacy column records are
translated to the named application records by the adapter; method signatures
do not accept `AsyncConnection` because transaction binding comes from the Unit
of Work.

| Repository operation | Normative typed signature | Temporary legacy alias mapped to it | Result/fence |
| --- | --- | --- | --- |
| Ensure identity | `ensure_identity(identity: ProfileIdentity) -> None` | `ensure_agent_profile_identity` | Idempotent only for the same tenant/profile identity; conflicting tenant/type or inactive identity raises `ProfileIdentityConflict`. |
| Acquire lifecycle lock | `acquire_lifecycle_lock(key: TenantProfileKey) -> None` | `acquire_agent_profile_lifecycle_lock` | Holds the transaction-scoped lifecycle advisory lock until Unit-of-Work exit. |
| Append revision | `append_revision(write: ProfileRevisionWrite) -> ProfileRevisionRecord` | `create_agent_profile_revision` | Persists the authority-supplied definition/hash and enforces `expected_previous_revision`; stale or failed writes raise `ProfileRevisionConflict`. |
| Get exact revision | `get_revision(selector: TenantRevisionSelector) -> ProfileRevisionRecord | None` | `get_agent_profile_revision` | Exact tenant/profile/revision and optional status only. |
| List latest revisions | `list_latest_revisions(query: LatestRevisionQuery) -> tuple[ProfileRevisionRecord, ...]` | `list_latest_agent_profile_revisions` | At most one highest revision per active same-tenant profile and optional status. |
| Record draft pointer | `record_draft(transition: DraftTransition) -> None` | `record_agent_profile_draft` | Updates only the aggregate latest/lifecycle facts defined by the replay contract. |
| Record publication | `record_publication(transition: PublicationTransition) -> None` | `record_agent_profile_publication` | Requires exact revision/hash selected by the authority; missing aggregate raises `ProfileAggregateConflict`. |
| Record withdrawal | `record_withdrawal(transition: WithdrawalTransition) -> None` | `record_agent_profile_withdrawal` | Requires the current published state and exact revision; stale state raises `ProfileRevisionConflict`. |
| Get aggregate | `get_aggregate(key: TenantProfileKey, lock: AggregateLock = NONE) -> ProfileAggregateRecord | None` | `get_agent_profile_aggregate` | `lock=UPDATE` acquires the existing aggregate row lock in the existing order. |
| Get current publication | `get_current_publication(selector: CurrentPublicationSelector, lock: AggregateLock = NONE) -> ProfileRevisionRecord | None` | `get_current_published_agent_profile` | Matches aggregate revision/hash/status and optional expected revision. |
| Get bound publication | `get_bound_publication(selector: BoundPublicationSelector, lock: AggregateLock = NONE) -> ProfileRevisionRecord | None` | `get_bound_published_agent_profile` | Exact tenant/profile/revision/content-hash publication only. |
| List current publications | `list_current_publications(query: PublishedProfileQuery) -> tuple[ProfileRevisionRecord, ...]` | `list_current_published_agent_profiles` | Same-tenant current publications; bounded limit `1..200`; query/category are typed filters. |
| List revision history | `list_revision_history(key: TenantProfileKey) -> tuple[ProfileRevisionRecord, ...]` | `list_agent_profile_revision_history` | Same-tenant immutable history in descending revision order. |

The application contract uses typed records such as `ProfileRevisionRecord`,
`ProfileAggregateRecord`, and `PublishedProfileProjection`. It does not expose an
untyped database row as the stable boundary. Record conversion belongs to the
PostgreSQL adapter; safe public projection remains an Agent Apps decision.

`ProfileRevisionWrite` contains the complete typed definition, actor identity,
lifecycle/provenance fields, `expected_previous_revision`, and canonical
`content_hash`; it replaces the current wide primitive argument list.
`TenantProfileKey` and every selector contain a non-empty tenant and agent key.
Lock mode is a closed enum, not a boolean/string supplied by transport. The
replay-contract slice must freeze each record field and current legacy error
code before implementation; removing or weakening a field is not permitted by
calling a raw row an equivalent record.

### 3.3 Unit of Work

`AgentProfileUnitOfWorkFactory.open(scope: AgentProfileScope, *, mode:
Literal["read", "write"]) -> AsyncContextManager[AgentProfileUnitOfWork]` is the
only application entry to persistence. `AgentProfileUnitOfWork` owns one
application transaction. It exposes the
transaction-bound `AgentProfileRepository`, `AuditLedgerWriter`, and required
cross-context client ports as typed properties for the lifetime of that Unit of
Work. Every exposed adapter instance is constructed over the same transaction;
their methods accept no connection or ambient transaction token. The
application never receives or imports a psycopg connection.

The Unit of Work:

- begins and ends exactly one transaction for one command;
- exposes no `commit()` call to transport;
- commits only after the use case and required durable audit facts succeed;
- rolls back every participating write on error;
- never opens a nested independent transaction inside a repository/client port;
- creates all transaction-bound port instances on entry and invalidates them on
  exit so they cannot outlive or be reused across the Unit of Work; and
- is constructed by bootstrap, not by the authority, transport, compatibility
  facade, or a default parameter.

On successful context exit, the factory commits after the service has appended
all required audit facts; on any exception or commit failure it rolls back and
the service returns no success result. There is no public `commit()`/`rollback()`
method. `__aenter__` returns the typed ports; `__aexit__` invalidates them even
when rollback itself fails. A service operation must leave the context before
its result crosses the application boundary.

Read-only queries may use a read-only transaction-scoped repository created by
the same factory. They do not create a process-global connection or adapter.

#### 3.3.1 Durable audit port

`AuditLedgerWriter.append(scope: AgentProfileScope, event:
AgentProfileAuditEvent) -> AuditAppendResult` is the only audit write available
to the service. `AgentProfileAuditEvent` is a closed tagged union with:

- `action`: one of `agent_profile.draft_saved`, `agent_profile.published`,
  `agent_profile.unpublished`, `agent_profile.draft_validated`,
  `agent_conversation.created`, or
  `agent_profile.test_conversation_created`;
- `target`: typed `AuditTarget(kind="agent_profile", agent_id=...)`;
- optional non-secret `trace_id`; and
- one action-specific payload: exact revision/content hash, expected and
  withdrawn revisions, validation content hash, or exact revision/session/purpose
  for Conversation creation.

`AuditAppendResult` contains only non-empty `audit_id`. The port accepts no
connection, arbitrary action string, target table name, untyped database row,
or transport payload. Its adapter uses the current Unit-of-Work transaction,
enforces the existing bounded JSON payload limit, maps oversize input to
`audit_payload_too_large`, and propagates write failure so the Unit of Work
rolls back the lifecycle transition. It must not open a second transaction,
swallow the failure, or return success before the audit row is durable.

### 3.4 Cross-context ports

Agent Apps application code calls other owners only through these asynchronous
typed contracts:

| Port operation | Normative signature | Result/error rule |
| --- | --- | --- |
| Principal authority | `PrincipalDirectory.require_principal(scope: AgentProfileScope) -> PrincipalFacts` | Returns same-tenant/workspace immutable identity facts or raises `ProfileForbidden`; never a session/header/database row. |
| Skill authority | `SkillAuthorityClient.resolve_release(scope: AgentProfileScope, selection: SkillSelection) -> SkillReleaseBinding` | Returns exact release/material identity or raises `ProfileDependencyUnavailable`/`ProfileDefinitionInvalid`. |
| MCP authority | `McpAuthorityClient.authorize_selection(scope: AgentProfileScope, selection: McpSelection) -> McpSelectionBinding` | Returns exact authorized tool selection or raises `ProfileForbidden`/`ProfileDependencyUnavailable`. |
| Conversation write | `ConversationClient.create_profile_conversation(scope: AgentProfileScope, command: CreateProfileConversation) -> ConversationReference` | Persists the exact `ProfileBinding` under Conversation ownership; write-capable adapter is obtained from the current Unit of Work. |
| Run replay read | `RunReadClient.read_replay_facts(scope: AgentProfileScope, run_id: str) -> RunReplayFacts` | Returns only immutable facts required to reauthorize a persisted profile binding; missing/cross-scope rows map to `ProfileNotFound`. |

These names describe application capabilities, not concrete modules. An adapter
may delegate to another context's `api.py`; it must not import that context's
repository or infrastructure module. Agent Apps must not absorb another
context's SQL merely to preserve one database transaction. Where a cross-owner
write must be atomic, the application obtains that client from the current
`AgentProfileUnitOfWork`; bootstrap's Unit-of-Work factory binds every exposed
client to the same transaction. A process-global or independently constructed
client is invalid for that operation. Each owner remains responsible for its
state transition.

### 3.5 Application error contract

Application errors are closed typed categories with stable machine codes. They
contain no SQL, table/constraint names, stack traces, secret values, or raw
dependency payloads. Transport maps them to HTTP independently.

| Error category | Required meaning |
| --- | --- |
| `ProfileDefinitionInvalid` | Typed definition or selector is malformed or violates Agent Apps product policy. |
| `ProfileForbidden` | Authenticated scope lacks admin, visibility, ACL, Skill, or MCP authority. Public get may intentionally collapse this to `ProfileNotFound`. |
| `ProfileNotFound` | Same-scope requested identity/revision/publication does not exist or is intentionally hidden. |
| `ProfileRevisionConflict` | Expected previous revision, lifecycle state, or concurrent writer fence is stale. |
| `ProfileBindingConflict` | Persisted `(agent_id, revision, content_hash)` does not match the authorized immutable binding. |
| `ProfileIdentityConflict` | Stable Agent/Profile identity exists with incompatible tenant, type, or lifecycle state. |
| `ProfileAggregateConflict` | Required mutable aggregate/pointer is absent or incompatible with the requested transition. |
| `ProfileDependencyUnavailable` | A required principal, Skill, MCP, Conversation, Run, audit, or persistence dependency cannot complete safely. |

The baseline machine-code mapping below is part of the replay contract. A code
keeps its current HTTP/repository status and collapse semantics until a separate
behavior issue changes it.

| Application category | Baseline machine codes and current collapse |
| --- | --- |
| `ProfileDefinitionInvalid` | `agent_id_invalid`, `agent_profile_avatar_asset_invalid`, `agent_profile_model_not_available`, `agent_profile_selector_conflict`, `agent_conversation_purpose_invalid`, `agent_conversation_operation_invalid`, and `agent_app_override_not_allowed` remain validation failures. |
| `ProfileForbidden` | `not_ai_admin`, `agent_profile_capability_not_available`, and `agent_profile_not_authorized` remain forbidden failures. Public detail intentionally collapses unauthorized/capability-invalid profiles to `agent_profile_not_found`; public list silently omits them. |
| `ProfileNotFound` | `agent_profile_not_found`, `agent_conversation_not_found`, `workspace_not_found`, and repository `run_not_found` keep their current scope-specific not-found semantics. |
| `ProfileRevisionConflict` | `agent_profile_create_revision_invalid`, `agent_profile_revision_stale`, `agent_profile_validation_unavailable`, and adapter `agent_profile_revision_write_failed` keep their current conflict/stale-write semantics. Route-level repository conflicts continue to collapse to `agent_profile_revision_stale` where they do today. |
| `ProfileBindingConflict` | `agent_profile_not_available`, `agent_profile_revision_invalid`, `agent_profile_session_mismatch`, `agent_profile_snapshot_invalid`, and `agent_profile_test_submission_conflict` keep their current immutable-binding, replay-snapshot, or idempotency-conflict semantics. |
| `ProfileIdentityConflict` | Adapter codes `agent_profile_identity_conflict` and `agent_inactive` remain stable identity failures. |
| `ProfileAggregateConflict` | Adapter code `agent_profile_aggregate_missing` remains the missing publication-pointer failure and is collapsed only where the current transport already collapses repository conflicts. |
| Audit input/dependency | `audit_payload_too_large` remains the bounded-payload failure. An uncoded database/dependency exception maps to `ProfileDependencyUnavailable` without exposing raw details; this decision does not invent a new public wire code. |

Worker-side bound-profile reauthorization intentionally returns `None` for a
missing/invalid/unauthorized binding instead of leaking one of these errors.
Changing that collapse, renaming a code, changing its status, or exposing
dependency details is a behavior change and requires its own evidence.

## 4. PostgreSQL adapter contract

`PostgresAgentProfileRepository` implements the application-owned repository
port in `app.agent_apps.infrastructure.postgres`. It may retain function-level
helpers internally, but the injected application dependency is the port
implementation rather than the infrastructure module.

The adapter owns:

- SQL and bounded row mapping for Agent Profile records;
- transaction-scoped advisory and row locks;
- expected-revision, content-hash, publication-status, and tenant fences;
- immutable revision insert and aggregate-pointer updates; and
- translation of database conflicts/not-found states into stable
  application-owned errors.

It must not:

- start, commit, or roll back a transaction supplied by the Unit of Work;
- select ACL, publication, admission, model, Skill, MCP, or public-projection
  policy;
- append audit facts through a hidden global repository;
- import HTTP transport, Chat, Runs, Worker, or bootstrap; or
- retain a fallback path through `app.repositories`.

## 5. Transaction, lock, and identity invariants

Migration must preserve these observable semantics:

1. **One Unit of Work.** Identity ensure, lifecycle lock, immutable revision
   append, aggregate transition, and required audit fact commit or roll back
   together for one command.
2. **Lifecycle lock first.** The transaction acquires the existing
   tenant/profile advisory lock before reading or mutating revision/aggregate
   state. A move must not reverse the current lock order.
3. **Immutable revisions.** A revision is append-only. The authority owns
   canonical definition serialization and SHA-256 computation; the repository
   persists the exact supplied definition/hash under the revision fence and
   must not silently recompute or replace either value. Existing revision
   numbers or hashes are never rewritten to simplify migration.
4. **Application stale-writer fence.** Draft, publish, and unpublish preserve
   the current combined invariant: lifecycle lock and authority validation occur
   before the transition, while immutable revision append enforces the expected
   previous revision. An aggregate update is not falsely treated as an
   independently sufficient CAS. Adding a new aggregate-level fence is a
   separate behavior/concurrency change.
5. **Exact admission identity.** New admission resolves one currently published
   revision/hash under current ACL and capability authority. Continuation,
   replay, and worker dispatch reauthorize the persisted exact pin and never
   silently advance to a newer publication.
6. **Tenant scope.** Every read, lock, mutation, and projection is scoped by the
   authenticated tenant and the required workspace/user facts.
7. **No partial audit.** A lifecycle write without its required durable audit
   fact, or an audit fact for a rolled-back write, is invalid.
8. **No policy replay in SQL.** The adapter returns typed records/failures;
   `AgentProfileAuthority` remains the only owner of ACL, visibility,
   publication, admission, and safe projection decisions.

Moving code that changes one of these rules is a behavior change, not a source
replay, and requires a separate issue plus PostgreSQL concurrency evidence.

## 6. Composition and dependency injection

`bootstrap.api` creates the production `AgentProfileUnitOfWork` factory,
PostgreSQL repository adapter, audit adapter, cross-context public clients, and
one complete `AgentProfileAuthority`/service graph. It installs that application
instance into the FastAPI dependency graph or app state.

Agent Apps transport obtains the service through framework dependency injection.
The dependency provider may read only the application instance installed by
bootstrap. A route must not import `app.bootstrap`, instantiate a PostgreSQL
adapter, or fall back to a module-global authority.

`bootstrap.worker` constructs the worker graph separately and passes the same
public Agent Apps service contract through worker entrypoints. Tests inject
fakes implementing application ports; fake adapters do not enter production
registries or `app/` solely for test convenience.

The following mechanisms are forbidden:

- `ContextVar`, thread/request local, connection attributes, or dynamic import
  used as a service locator;
- module-level mutable service registration or a hidden singleton fallback;
- zero-argument application construction that imports concrete infrastructure;
- transport-to-bootstrap or application-to-infrastructure imports;
- passing `app.repositories` as the injected port; and
- using `getattr`, strings, or unrestricted module/class paths to select an
  adapter.

## 7. Compatibility and deletion

`app.repositories` and `app.agent_profiles` are migration surfaces, not target
APIs. Compatibility is retained only for named consumers that cannot migrate in
the same bounded sequence.

Before retaining or removing a compatibility surface, the issue/PR records:

- exact import and symbol inventory, including supported external callers;
- canonical `app.agent_apps.api` replacement;
- whether historical persisted rows require dual-read behavior;
- contract tests proving identity/delegation and fail-closed errors;
- removal condition and rollback constraint; and
- observable usage evidence when the import/route is independently deployed.

The complete compatibility-observation and deletion-proof requirements in
[`source-code-architecture.md` sections 7 and 8](source-code-architecture.md#7-compatibility-contract)
remain mandatory. For a deployed surface, evidence includes the exact
source/image/config subject, reproducible inventory or telemetry query,
observation start/end, tenant and consumer scope, a predeclared window covering
at least one normal rollout/use cycle, owner attestation, and known blind spots.
If telemetry is unavailable or a complete supported consumer inventory is
unavailable, deletion is blocked. An import facade also must satisfy the
import-compatibility proof tier; elapsed time alone is never an exit condition.

Internal callers migrate to the injected application service. Once every
supported caller has migrated, bridge retirement remains two bounded changes:
first remove the immutable architecture authority entry while keeping aliases
stable; then delete the aliases/import facade under the next authority. No
calendar-only compatibility window is created.

The target removes:

- the thirteen Agent Profile identity aliases from `app.repositories`:
  `acquire_agent_profile_lifecycle_lock`, `create_agent_profile_revision`,
  `ensure_agent_profile_identity`, `get_agent_profile_aggregate`,
  `get_agent_profile_revision`, `get_bound_published_agent_profile`,
  `get_current_published_agent_profile`,
  `list_agent_profile_revision_history`,
  `list_current_published_agent_profiles`,
  `list_latest_agent_profile_revisions`, `record_agent_profile_draft`,
  `record_agent_profile_publication`, and `record_agent_profile_withdrawal`;
- Agent Apps use of global repository error and audit helpers;
- the module-global authority in `app.agent_profiles`;
- route-local authority construction; and
- obsolete facade wrappers after their consumer inventory is empty.

It does not bulk-rewrite persisted Agent Profile revisions or conversation/run
pins. Historical compatibility follows the owning data contract.

## 8. Migration slices

1. **Decision.** Merge this source decision without production changes.
2. **Replay contract.** Freeze typed persistence records, SQL/lock/transaction
   behavior, errors, and lifecycle side effects at the current boundary.
3. **Application ports.** Add repository, Unit-of-Work, audit, and peer-client
   protocols plus pure application records; no concrete imports.
4. **PostgreSQL adapter.** Implement the repository/Unit-of-Work adapters by
   replaying the existing canonical SQL once; do not duplicate or redesign it.
5. **Application service.** Inject ports into `AgentProfileAuthority`, remove its
   direct global repository dependency, and preserve every response/error and
   private/public projection.
6. **API composition.** Build the graph in `bootstrap.api` and inject it into
   Agent Apps transport. Remove route-local and compatibility-global authority
   construction.
7. **Peer migration.** Move Chat, Runs, Chat Sessions, and Worker callers to the
   public Agent Apps application contract, one consumer group at a time.
8. **Bridge retirement.** Inventory external imports, retire the Agent Profile
   migration bridge authority-only, then delete repository aliases and the old
   facade in the following change.
9. **Behavior changes.** Address product/security changes such as raw-selector
   policy only after ownership is stable and under their own evidence.

One slice must not combine source ownership replay with SQL, lock, transaction,
ACL, publication, projection, persisted identity, or wire behavior changes.

## 9. Required evidence

The production migration is not complete until focused evidence covers:

- pure domain/application unit tests with explicit fake ports;
- executable protocol tests asserting every service/repository/peer-port
  signature, closed input/result type, application-error category, and absence
  of connection/transport/database types;
- a one-to-one contract test mapping all thirteen governed legacy aliases to the
  repository operations in section 3.2, with no missing or additional alias;
- Unit-of-Work tests proving clean-exit commit, exception/commit-failure
  rollback, same-transaction port binding, no nested transaction, and rejection
  of port use after context exit;
- audit-port contract tests for the six closed actions, typed action payloads,
  returned `audit_id`, payload-size rejection, and rollback when audit insert
  fails;
- an exact error-code/status/collapse matrix for every code in section 3.5,
  including public forbidden-to-not-found collapse, list omission, route-level
  repository-conflict collapse, and worker reauthorization returning `None`;
- port and public-API contract tests with no infrastructure import;
- exact ordinary-user projection allowlist tests for public catalog, detail,
  and Conversation recovery, including recursive absence of every forbidden
  field in section 3.1.1 and `extra="forbid"` for unknown fields;
- separate administrator projection tests proving same-tenant `is_ai_admin`
  gating, the closed admin allowlist, and continued exclusion of storage,
  runtime, executor-private, and secret-like data;
- deterministic replay of every current repository result/error at the
  canonical adapter and temporary legacy boundary;
- PostgreSQL integration tests for transaction rollback, lifecycle lock
  contention/order, expected-revision conflict, immutable revision/hash,
  publish/unpublish fencing, tenant isolation, and atomic audit facts;
- admission/replay tests for exact revision/hash, current ACL/capability
  reauthorization, withdrawn publication, and no private projection leakage;
- route tests with an explicitly injected application service;
- Chat, Runs, Chat Sessions, and Worker tests with the same public contract;
- architecture tests rejecting application-to-infrastructure,
  transport-to-bootstrap, service-locator, module-global construction, and new
  `app.repositories` dependencies;
- exact base/head, immutable governance, and independent fixed-SHA review; and
- facade deletion proof matching the import-compatibility tier.

Source and CI evidence do not prove production deployment, PostgreSQL rollout,
ordinary-user Agent Market behavior, or mixed-version runtime safety. Those
remain separate external acceptance gates.
