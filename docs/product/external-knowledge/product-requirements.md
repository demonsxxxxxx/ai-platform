# External Knowledge Product Requirements

## 1. Product outcome

An administrator connects AI Platform to the company's existing RAGFlow
service, synchronizes an authorized catalog of existing datasets, assigns
visibility, and binds one or more knowledge sources to an Agent Profile
Revision. An authorized employee starts that published expert from the Agent
Market, asks a question, receives an answer grounded in retrieved evidence, and
can inspect stable source citations without knowing RAGFlow, dataset IDs,
credentials, models, prompts, MCP, or retrieval infrastructure.

## 2. Problem statement

The company already operates RAGFlow and has completed document parsing and
indexing. AI Platform lacks a product domain that can safely expose those
knowledge sources to published enterprise experts. A useful integration must
solve six user-facing problems:

1. administrators need one controlled connection and an understandable source
   catalog;
2. knowledge visibility must follow company identity, role and department
   facts;
3. Agent authors need a multi-select knowledge binding that survives
   publication and conversation pinning;
4. every run must reauthorize the principal and exact bound sources;
5. answers must retain inspectable evidence and stable citations; and
6. upstream absence, authorization denial, empty results and invalid responses
   must produce distinct, truthful outcomes.

## 3. Product principles

### 3.1 One enterprise identity scope

The product serves one enterprise with many concurrent users. Administrators,
ordinary users, roles, departments and explicit user grants are the visible
authorization concepts. The existing internal `tenant_id="default"` deployment
scope may continue to fence persistence and queries; it is not a configurable
product concept or user-facing field.

### 3.2 Knowledge is an Agent resource

A Knowledge Source is governed data available to an Agent Profile Revision. It
is independent of the Agent Skill Set. Selecting a Skill does not grant a
knowledge source, and selecting a knowledge source does not create or invoke a
Skill.

### 3.3 The provider owns knowledge preparation

RAGFlow remains the authority for datasets, document ingestion, parsing,
chunking, embeddings and indexes. AI Platform stores bounded projections and
evidence receipts required for authorization, execution, history and audit.

### 3.4 The platform owns retrieval admission

Browser requests and model-generated arguments cannot choose a provider URL,
credential, raw dataset identity, ACL or unrestricted retrieval filter. The
platform resolves all provider-facing values from the admitted Agent revision,
current principal and server-owned policy.

### 3.5 Evidence precedes grounded success

A knowledge-required run may produce a grounded answer only from a validated,
authorized evidence set. Availability failures, authorization failures,
invalid provider responses and zero-evidence results have explicit outcomes.

### 3.6 History remains understandable

Citations displayed in a completed message remain readable after provider
catalog changes. The history projection relies on a bounded citation snapshot,
not a live provider lookup.

## 4. Actors

| Actor | Need | Granted surface |
| --- | --- | --- |
| Platform administrator | Configure and verify the company RAGFlow connection | Connection administration and safe health state |
| Knowledge administrator | Synchronize datasets and govern source visibility | Knowledge catalog, source ACL and retrieval test |
| Agent author | Bind authorized sources and retrieval policy to an expert | Agent Builder draft, validation, test and publish |
| Ordinary employee | Ask an expert and inspect sources | Agent Market, Workspace, answer and citation drawer |
| Auditor | Explain access, retrieval and citation outcomes | Redacted audit and run evidence projections |
| Runtime operator | Diagnose provider availability and latency | Health, metrics and safe failure classes |

One principal may hold more than one role. Product authorization is evaluated
from current server-owned identity facts.

## 5. In-scope user journeys

### 5.1 Connect the existing RAGFlow service

1. A platform administrator opens Knowledge Connections.
2. The administrator enters a display name, internal base URL and API key.
3. The platform validates the URL and stores the credential through the
   server-owned secret boundary.
4. The administrator starts one idempotent candidate-activation operation. The
   server binds its sole stored candidate revision, performs the authenticated
   capability check and enumerates that revision's complete catalog.
5. The administrator sees `active` only after the activation operation commits
   the candidate revision and its complete catalog generation as one authority
   pair.
6. Rotating a credential or transport policy creates a candidate revision; the
   current revision remains active until the same activation operation checks,
   synchronizes and atomically switches the candidate.
7. Changing provider key or canonical base URL creates another connection, so
   existing logical dataset identities never move between provider authorities.
8. A candidate failure leaves the prior revision, catalog and freshness
   unchanged.
9. Run admission and connection lifecycle transitions serialize on a monotonic
   connection epoch. A snapshot may use its pinned revision only when an
   immutable lifecycle receipt binds that revision to the stored admission
   epoch, the caller is the exact current attempt and its deadline remains.
10. User-visible retry, resume and copy operations create a new Run identity and
    immutable snapshot after reauthorizing against the current connection state
    and active revision. Duplicate delivery or worker recovery of the exact
    current attempt keeps its existing snapshot, generation and deadline.

### 5.2 Synchronize the existing dataset catalog

1. A knowledge administrator starts synchronization for an active connection.
2. The platform reads every accessible provider page with bounded requests.
3. Existing logical sources are updated by stable provider identity.
4. New provider datasets become `pending_review` logical sources pending ACL
   review.
5. A provider source absent from a complete successful synchronization becomes
   `missing`; it is not deleted.
6. A partial or failed synchronization leaves the prior complete catalog
   authoritative.

### 5.3 Configure source visibility

1. A knowledge administrator selects one logical source.
2. The administrator chooses enterprise-wide or restricted visibility.
3. Restricted visibility uses the existing department, role and explicit-user
   selectors.
4. The platform validates every selected authority value against current
   catalogs.
5. A source becomes selectable in Agent Builder only after it is active and its
   ACL is valid.

### 5.4 Bind knowledge to an Agent Profile Revision

1. An Agent author opens a draft.
2. Enterprise Knowledge is off by default for that expert; the author must
   explicitly enable it for the new Agent Profile Revision.
3. When enabled, the author selects one to eight active knowledge sources.
4. The author selects a governed retrieval profile.
5. The Builder shows source names, visibility summaries and connection health;
   it does not show provider resource IDs or credentials.
6. Disabling the capability may retain authoring choices, but no executable
   Knowledge binding or Run snapshot may be derived from them.
7. Draft save preserves the selected logical source identities.
8. Publish validates that the Agent visibility is contained by every required
   source visibility.
9. Publication always stores the explicit enable flag. An enabled revision
   also stores the exact executable bindings and retrieval profile version; a
   disabled revision stores no executable Knowledge bindings.

### 5.5 Use a knowledge-backed expert

1. The Market shows a published Agent only when the current principal can use
   the Agent and every required source.
2. Opening the Agent Workspace does not create a conversation.
3. Explicit send creates or uses a revision-pinned Agent Conversation.
4. Run admission reauthorizes publication, principal, Agent ACL, source ACL,
   source status and retrieval policy.
5. The worker retrieves from each admitted source before Engine dispatch.
6. The platform validates, bounds, deduplicates and fuses evidence.
7. The Engine receives engine-neutral evidence with stable evidence IDs.
8. The durable answer references only accepted evidence IDs.
9. The employee can open citations and inspect title, bounded excerpt, score or
   relevance label, and provider-supplied position when available.

### 5.6 Diagnose an unsuccessful knowledge run

1. A provider connectivity failure produces a safe unavailable state.
2. An authorization failure denies dispatch.
3. A missing or disabled bound source denies dispatch.
4. An invalid provider response fails the knowledge step.
5. A valid zero-result response produces a deterministic no-evidence result.
6. Operator and audit views receive safe correlation and failure classes while
   ordinary users receive actionable, non-sensitive guidance.

## 6. Product surfaces

### 6.1 Knowledge Connections

The administration surface shows connection name, provider, safe URL origin,
status, last authenticated check, last complete synchronization, source count
and safe failure class. Credential values are write-only.

### 6.2 Knowledge Sources

The catalog supports bounded pagination, name search, connection/status
filters, manual synchronization, local display aliases, source visibility and
retrieval testing. It does not expose document ingestion controls in the first
release. A governed link may open the company RAGFlow administration surface.

A source retrieval test is an administrator-only, provider-bound diagnostic.
It returns a bounded ephemeral evidence preview and a redacted audit fact. It
does not create an Agent Conversation, Run, message, evidence row, citation row
or ordinary-user history item.

### 6.3 Agent Builder

Knowledge appears as a progressive configuration section separate from Skill
Set and MCP tools. It supports multiple sources, one retrieval profile, source
health warnings, ACL compatibility validation and a draft-only retrieval test.
The draft test uses the existing Builder Test Conversation and governed Run
path, so its durable facts remain test-scoped and never enter Market or
ordinary-user conversation history.

### 6.4 Agent Market and detail

Authorized principals see a safe knowledge capability summary and freshness
time. Public projections do not reveal provider names, internal URLs, raw
dataset identities, ACL lists or credentials.

### 6.5 Agent Workspace

The Workspace retains the shared bounded working state while retrieval runs. A
completed answer shows inline citation markers and a source drawer. Refresh and
reconnect hydrate the same citation projection from durable records.

### 6.6 Audit and operations

Audit projections contain principal, Agent revision, logical source IDs,
retrieval profile version, outcome, result count, latency, provider-safe error
class and evidence digests. They do not contain raw API keys, provider URLs,
queries, chunk text, private instructions or model-private traces.

## 7. Release boundaries

### 7.1 First release

- one provider key: `ragflow`;
- one or more configured company RAGFlow connections;
- existing dataset catalog synchronization;
- source ACL and Agent multi-source binding;
- deterministic retrieval before Engine dispatch;
- bounded parallel fan-out and rank fusion;
- durable citation snapshots;
- history and terminal-hydrate citation projections; and
- administration, Builder, Market and Workspace surfaces.

### 7.2 Follow-on release

- Agent-directed retrieval through a generic platform capability;
- structured metadata filters selected from an administrator-approved schema;
- scheduled catalog synchronization;
- provider document deep links when a safe stable URL contract exists; and
- provider-neutral additional adapters.

### 7.3 Product exclusions

The first release does not own document upload, parsing configuration, chunk
editing, embedding selection, index construction, RAGFlow deployment, provider
backup, provider user administration, external document ACL mutation, or
RAGFlow Chat/Agent execution.

## 8. Success measures

The product is acceptable when:

1. an administrator can connect and synchronize the existing provider without
   exposing its credential;
2. an Agent author can publish one expert bound to multiple authorized sources;
3. an authorized ordinary user can obtain a cited answer and inspect its source;
4. an unauthorized principal cannot discover, bind, query or cite a restricted
   source;
5. provider absence and zero evidence never appear as a grounded success;
6. reconnect and history preserve the accepted citations exactly once; and
7. source, CI, deployment, runtime and external-acceptance evidence remain
   separately identified.
