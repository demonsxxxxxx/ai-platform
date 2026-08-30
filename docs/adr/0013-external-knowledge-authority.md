---
status: accepted
decision_issue: 1311
---

# Establish External Knowledge as a bounded product authority

## Context

The company already operates RAGFlow and owns document ingestion, parsing,
chunking, embedding, and indexing there. AI Platform needs to let an
administrator connect that service, project its existing datasets as governed
Knowledge Sources, bind multiple authorized sources to an immutable Agent
Profile Revision, retrieve evidence for an admitted Run, and render durable
citations.

The repository currently contains a seeded RAGFlow Skill, MCP tool, and Agent.
Those identities demonstrate capability selection, but they do not own a
provider connection lifecycle, dataset catalog, source authorization version,
Run snapshot, evidence receipt, or citation history. Extending those special
cases would make generic MCP policy, Agent publication, and knowledge lifecycle
compete for the same business facts.

## Decision

`knowledge` is a bounded context in the domain-first modular monolith.

1. Knowledge owns provider connections and immutable connection revisions,
   logical Knowledge Sources, source authorization versions, retrieval
   profiles, catalog synchronization receipts, Run Knowledge Snapshots,
   normalized evidence, citation snapshots, and their safe administrative and
   user projections.
2. RAGFlow is the first provider adapter. Its URL, dataset identity, response
   payload, and error details terminate inside Knowledge infrastructure. The
   adapter resolves an admitted credential reference from shared secret
   infrastructure only for the bounded provider call. Browser, Agent Profile,
   Run, Conversation, and Engine contracts use platform logical identities and
   redacted projections.
3. `mcp` continues to own the generic MCP server/tool catalog and authorization.
   A later agent-directed `knowledge_search` tool may delegate to the Knowledge
   API; it does not become a second connection, source, retrieval, or citation
   authority.
4. `agent_apps` owns the immutable selection of logical source IDs and one
   retrieval-profile version on each Agent Profile Revision. It asks Knowledge
   to validate current source authorization and readiness when saving,
   publishing, and admitting that revision.
5. `runs` owns the admission transaction. `runs.AdmitRun` creates one Unit of
   Work and calls the Knowledge API with that transaction so the Run and its
   immutable Knowledge Snapshot commit or roll back together before dispatch.
6. `conversations` owns assistant-message finalization. Its finalization Unit of
   Work calls the Knowledge API so the durable assistant message and bounded
   citation snapshot commit or roll back together.
7. Knowledge owns an opaque `secret_ref` and the authorization to resolve it for
   an admitted provider call. Shared secret infrastructure owns encrypted
   credential bytes, encryption keys, and physical secret lifecycle.
   Model/Engine credentials and external-Knowledge credentials use distinct
   purpose namespaces and resolution authorities; neither owner may resolve the
   other's reference. Administrative projections expose only credential state,
   fingerprint metadata, and safe URL origin.
8. The deployment remains one internal enterprise. The existing internal
   deployment-scope key is supplied by authenticated server context and is not
   a browser field, route parameter, or configurable Knowledge concept.
9. Provider calls are bounded, deny redirects, validate the configured absolute
   HTTP or HTTPS origin against deployment egress policy, use server-resolved
   dataset identities, and return typed safe outcomes. Engine adapters receive
   only normalized evidence selected by the platform and never resolve a
   Knowledge credential reference.

## Source placement

```text
app/knowledge/
  api.py
  domain/
  application/
  infrastructure/
  transport/
```

Transport validates and projects HTTP. Application use cases own transactions,
idempotency, synchronization, and provider-call ordering. Domain modules own
framework-neutral lifecycle and authorization values. Infrastructure owns
PostgreSQL and RAGFlow translation. Bootstrap constructs the concrete ports.

## Lifecycle and migration

Knowledge tables are additive and retain immutable connection revisions,
authorization versions, snapshots, evidence, and citations needed by historical
Runs. Existing seeded RAGFlow identities remain readable during migration. New
Knowledge writes use only the Knowledge API. Their writers are retired after
the exact consumer, persistence, and rollback gates in the External Knowledge
baseline disposition are satisfied.

Application rollback disables new Knowledge-bound admission and keeps the added
tables readable. It does not drop connection, source, binding, snapshot,
evidence, or citation records.

## Consequences

- Administrators receive a coherent connection and source-governance product.
- Agent authors select stable logical sources instead of provider datasets or
  transport tools.
- Provider replacement changes one Knowledge infrastructure adapter while the
  Agent, Run, Conversation, Engine, and frontend contracts remain stable.
- Run admission and message finalization require explicit cross-context Unit of
  Work tests because each transaction commits facts owned by two contexts.
- The current seeded path needs a measured cutover; it is not runtime evidence
  for the new product.

## Evidence boundary

This decision and source governance establish ownership and placement. Source,
unit, integration, PostgreSQL, frontend build, packaged-image, deployment,
runtime, and company RAGFlow acceptance remain separate evidence layers.
