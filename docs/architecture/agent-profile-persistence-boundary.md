# Agent Profile Application and Persistence Boundary

Status: normative source-architecture decision

Owner: `agent_apps` bounded context

Parent contract: [`source-code-architecture.md`](source-code-architecture.md)

Product authority: [GitHub Issue #701](https://github.com/demonsxxxxxx/ai-platform/issues/701)

## 1. Decision

Agent Profile revision, publication, visibility, and admission are one Agent Apps
application capability. They are not generic repository, route, Skill, MCP,
Conversation, or Run concerns.

```text
Agent Apps HTTP transport -+
Chat / Runs application ----+--> AgentProfileAuthority
Worker application ---------+             |
                                          +--> agent_apps.infrastructure.postgres
                                          +--> Skill and MCP authorities
                                          +--> identity, Conversation, Run, and audit authorities
```

`AgentProfileAuthority` is the lifecycle and admission policy owner. Agent Profile
persistence calls go directly to `app.agent_apps.infrastructure.postgres`.
Other contexts call the narrow contracts exposed by `app.agent_apps`; the global
repository module does not export Agent Profile persistence operations.

## 2. Source Ownership

| Concern | Canonical owner | Responsibilities |
| --- | --- | --- |
| Profile definition rules | `app.agent_apps.domain` | canonical values, normalization, immutable revision hash input |
| Lifecycle and admission | `app.agent_apps.authority.AgentProfileAuthority` | draft, publish, withdraw, public access, exact-pin admission and reauthorization |
| Public in-process values | `app.agent_apps.api` | Agent Profile constants, projections, Skill-set normalization and pinning |
| PostgreSQL persistence | `app.agent_apps.infrastructure.postgres` | lifecycle locks, revision and aggregate SQL, publication queries |
| HTTP transport | `app.routes.agent_profiles` | authentication, request translation, transaction scope, response mapping |
| Peer consumers | Chat, Runs, and Worker | construct and call `AgentProfileAuthority` methods without a delegation facade |

The context owns `agent_profiles`, immutable `agent_profile_revisions`,
publication state, ACL decisions, and the private admitted definition derived
from one exact published revision. It does not own Skill release state, MCP
catalog state, Conversation history, Run lifecycle, principal identity, or the
generic audit ledger.

## 3. Canonical Profile Contract

A profile definition contains only:

- `name`
- `description`
- `starter_prompts`
- private `instructions`
- `skill_set`
- `mcp_tool_ids`
- `avatar_ref`
- `avatar_seed`
- `market_tags`
- `visibility`
- `allowed_department_ids`
- `allowed_roles`
- `allowed_user_ids`

`skill_set` is the only Skill configuration. Each item contains one `skill_id`;
the platform resolves and pins the authoritative Skill version during admission.
Model selection remains Run-owned and is not part of the profile definition.

The public projection contains only:

- `agent_id`
- `revision`
- `name`
- `description`
- `starter_prompts`
- `avatar_ref`
- `avatar_seed`
- `market_tags`
- `completed_tasks`
- `published_at`
- `is_favorite`

The admin projection additionally contains lifecycle status, private instructions,
Skill and MCP selections, visibility ACLs, immutable content hash, and lifecycle
timestamps. Public projections never contain private instructions, MCP identifiers,
Skill storage or runtime identifiers, content hashes, model configuration, ACL
membership, or executor payloads.

Request and projection models reject unknown fields. New fields require a contract
change across models, domain normalization, hashing, persistence, projections,
tests, frontend types, and documentation in one change.

## 4. Lifecycle

The mutable `agent_profiles` aggregate identifies the latest revision and current
publication. `agent_profile_revisions` is append-only history.

- Draft save requires an explicit create or update revision precondition.
- Publish revalidates the selected draft and writes a new immutable published
  revision.
- Withdraw appends a withdrawn revision and clears the current publication.
- Lifecycle writes hold the tenant/profile advisory lock and enforce the expected
  revision fence.
- Existing revision numbers and hashes are never rewritten.
- Publication reads require both the aggregate publication pointer and matching
  immutable revision status.

The revision content hash is computed from the canonical profile definition only.
Publication metadata and aggregate pointers are not hash inputs.

## 5. Admission And Reauthorization

New conversations resolve the current published profile. Existing conversations
and Runs resolve their exact persisted `(agent_id, revision, content_hash)` pin.
Every admission and replay:

1. authorizes the current principal and profile ACL;
2. verifies that the Agent identity remains active;
3. validates the immutable profile definition;
4. resolves current Skill and MCP authority;
5. materializes exact governed Skill versions;
6. produces the executor-private profile input; and
7. preserves the exact profile pin on Session and Run records.

Historical tool calls do not restore current capabilities. Worker dispatch and
Run replay reauthorize the exact profile pin against current principal, Agent,
Skill, and MCP authority before execution.

## 6. Persistence Contract

`app.agent_apps.infrastructure.postgres` is the sole SQL owner for:

- `acquire_agent_profile_lifecycle_lock`
- `ensure_agent_profile_identity`
- `create_agent_profile_revision`
- `get_agent_profile_aggregate`
- `get_agent_profile_revision`
- `get_bound_published_agent_profile`
- `get_current_published_agent_profile`
- `list_agent_profile_revision_history`
- `list_current_published_agent_profiles`
- `list_latest_agent_profile_revisions`
- `record_agent_profile_draft`
- `record_agent_profile_publication`
- `record_agent_profile_withdrawal`

The schema stores only the canonical profile fields listed in section 3. The final
schema operation drops superseded Agent Profile columns and trigger functions so
an upgraded database exposes one write contract.

## 7. Security And Operational Invariants

- Every query and lifecycle lock is tenant-scoped.
- Visibility ACL checks fail closed.
- Public projection is an allowlist and cannot expose executor-private data.
- Profile admission cannot override Run-owned model selection.
- Skill and MCP identifiers are accepted only from the immutable authorized
  profile definition.
- Conversation and Run pins cannot silently advance to another publication.
- Database errors do not trigger alternate write paths.
- Deployment applies the schema before starting application processes built for
  this contract.

## 8. Verification

A behavior change must cover:

- strict request and projection rejection of unknown fields;
- deterministic canonical hash behavior;
- lifecycle stale-writer and immutable-history fences;
- exact-pin admission, replay, and worker reauthorization;
- tenant, visibility, principal, Skill, and MCP authorization failures;
- public non-disclosure;
- current frontend request and projection types;
- schema idempotence and absence of superseded Agent Profile columns, triggers,
  imports, aliases, selectors, fixtures, and documentation.

Local tests are developer evidence. Trusted merge authority remains the required
GitHub checks bound to the actual base and head.
