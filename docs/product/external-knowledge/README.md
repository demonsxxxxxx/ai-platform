# External Knowledge Product Contract

Design ID: `ai-platform.external-knowledge.v1`

## Purpose

This folder is the single product-requirement index for attaching an
enterprise-managed external knowledge source to an immutable Agent Profile
Revision and using retrieved evidence in an authorized Agent Run.

The first supported provider is the company's existing RAGFlow service. That
service already owns document ingestion, parsing, chunking, embedding, and
indexing. AI Platform owns the governed connection, catalog projection,
principal authorization, Agent binding, retrieval admission, evidence receipt,
citation projection, and user experience.

## Normative owners

| Concern | Normative owner |
| --- | --- |
| Product outcome, actors, scope, journeys, user-visible rules | [`product-requirements.md`](product-requirements.md) |
| Atomic functional requirements | [`functional-requirements.md`](functional-requirements.md) |
| Domain vocabulary, authority, states, data, APIs, provider and event contracts | [`domain-and-api-contract.md`](domain-and-api-contract.md) |
| Source, integration, browser, runtime and external acceptance | [`acceptance-matrix.md`](acceptance-matrix.md) |
| Requirement-to-test-to-change traceability | [`traceability-matrix.md`](traceability-matrix.md) |
| Independently acceptable implementation change contracts | [`implementation-slices.md`](implementation-slices.md) |
| Machine-readable atomic-case and changed-path ownership | [`manifests/`](manifests/) |
| Current RAGFlow-specific surface inventory and migration disposition | [`baseline-disposition.md`](baseline-disposition.md) |

If a summary in one document differs from the detailed owner above, the
detailed owner is normative for that concern.

Every candidate implementation slice publishes one versioned manifest in
`manifests/` as part of its reviewed change.
The required repository validator derives that slice's exclusive atomic cases
from `traceability-matrix.md` and binds the manifest to the exact changed paths
before merge. Manual workflow dispatch validates repository authority; pull
request and push subjects additionally validate exact changed-path coverage.

## Repository authority dependencies

This product contract composes with, and does not replace:

- [`../../../CONTEXT.md`](../../../CONTEXT.md) for Agent App domain language;
- [`../../architecture/runtime-authorities.md`](../../architecture/runtime-authorities.md)
  for runtime ownership;
- [`../../architecture/source-code-architecture.md`](../../architecture/source-code-architecture.md)
  for bounded-context placement and dependency direction;
- [`../../architecture/single-enterprise-data-lifecycle.md`](../../architecture/single-enterprise-data-lifecycle.md)
  for single-enterprise identity, data ownership, payload bounds and migration;
- [`../../adr/0001-agent-app-revision-authorization-lifecycle.md`](../../adr/0001-agent-app-revision-authorization-lifecycle.md)
  for revision pinning and per-run reauthorization;
- [`../../architecture/redis-streams-sse-event-channel.md`](../../architecture/redis-streams-sse-event-channel.md)
  for public event transport; and
- [`../../acceptance/agent-app/ordinary-user-matrix.md`](../../acceptance/agent-app/ordinary-user-matrix.md)
  for the source/runtime evidence boundary.

The current runtime authority groups external knowledge under MCP. Implementing
this contract requires an accepted ADR that establishes `knowledge` as the
product authority and limits MCP to an optional execution adapter. No product
code may create a second knowledge authority before that decision is accepted.

## Product boundary

```text
Company RAGFlow
  owns: datasets, documents, parsing, chunks, embeddings, indexes
                         |
                         | authenticated provider API
                         v
AI Platform Knowledge authority
  owns: connection, source catalog, ACL, Agent binding, retrieval policy,
        run snapshot, evidence receipt, citations, audit and projections
                         |
                         | engine-neutral evidence
                         v
AI Platform Execution adapter
  owns: Claude Agent SDK prompt/tool translation only
                         |
                         v
Shared Run, message, SSE and history authorities
```

## Release scope

The first product release contains:

1. one governed RAGFlow connection type;
2. synchronized projections of existing RAGFlow datasets;
3. single-enterprise department, role and user visibility;
4. multiple knowledge sources bound to an immutable Agent Profile Revision;
5. deterministic retrieval before Engine dispatch;
6. bounded multi-source fusion;
7. durable citation snapshots and history rendering; and
8. explicit availability, authorization and no-evidence outcomes.

Agent-directed retrieval through a generic `knowledge_search` capability is a
separate follow-on release after the deterministic retrieval path is accepted.
