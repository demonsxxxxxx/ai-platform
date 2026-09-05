# Runtime Authority Map

Status: business ownership contract. Target package names are not evidence that
all callers have migrated. See [system architecture](system-architecture.md) for
the process map and [source architecture](source-code-architecture.md) for imports.

AI Platform is a single-enterprise, multi-user Expert Agent application. The
platform owns admission, authorization, durable facts, and public projections.
The Harness SDK owns the private model/tool loop within its admitted scope.

## Business facts

| Fact or decision | Business owner | Allowed adapters and consumers |
| --- | --- | --- |
| Principal and authentication lifetime | Identity and the existing company authority | Browser/API authentication; ADR 0007 owns the fixed authentication day |
| Profile revision, publication and ACL | Agent Apps | Admin/public projections and exact-bound admission |
| Skill version, release, distribution | Skills | Governed catalog and materialization; archive preserves history |
| Conversation text and session ownership | Conversations | Context selection and authorized history projections |
| Context selection and exact executor input | Context | Engine receives the materialized snapshot; SDK session memory is not authority |
| Run admission, attempt and business terminal outcome | Runs | API, Worker and reconciler request transitions through the same owner |
| Queue scheduling and dispatch orchestration | Execution | Redis is transport and a lease projection; it does not decide business success |
| Provider resource lifecycle and callback receipt | Sandbox | OpenSandbox/Docker provider port; authenticated callback transport |
| Public event projection, publication and replay | Streaming | Safe committed facts only; no independent Run terminalization |
| Input-file identity, binding and access | Files | Object storage and authorized staging |
| Output artifact identity, lineage and eligibility | Artifacts | Authorized collection and download; model text cannot create an artifact fact |
| Physical deletion workflow | Object Lifecycle | Target-owned eligibility plus one claim/receipt outbox |
| Model catalog, selection and pinned connection | Execution, with admitted binding written by Runs | Trusted model proxy and Engine adapter; provider secrets stay outside execution input |

Run-event storage does not confer business authority on Streaming. Runs decides
Run lifecycle facts; Sandbox admits callback receipts; the transaction-scoped
ledger persists what the owning operation supplies. A shared transaction must
not become a second cross-domain policy engine.

## Distinct lifecycle facts

A Run outcome, a RunAttempt execution fence, a queue lease, a Sandbox resource
state, a stream incarnation, and a browser connection generation answer different
questions. Do not collapse them into one `status` or universal generation.
A terminal Run may have cleanup pending. A disconnected browser may observe a
Run that is still executing. A valid callback during asynchronous handoff may
remain acceptable even though a scheduling worker is no longer active.

## Process boundaries

API authenticates commands and reads, persists callbacks through their owners,
and exposes projections/SSE. Worker dispatches admitted work. Executor runs the
private Engine loop in the Sandbox. Reconciler settles asynchronous observations
and cleanup through the same Runs/Sandbox authorities. Maintenance schedules
bounded work; it is not another source of business truth.

The existing deployment runs maintenance and reconciliation with Worker. An
independent maintenance process is a proposed packaging/runtime change, not a
currently deployed component inferred from a target directory.

## Compatibility and replacement

A compatibility module may translate a name, request, response, or historical
record and delegate. It may not make another admission, authorization,
publication, lifecycle, or execution decision. Historical read support is
separate from permission for old producers to create new facts.

Base Harness chat uses `execution_kind=harness_chat`, no `skill_id`, and empty
Skill authority. A Skill run uses exact admitted Skill/version/release facts.
An Engine implementation is not a synthetic default Skill.

Replacing Claude with another Harness changes only the private Engine adapter
and SDK translation. It must preserve the platform API, Profile/Skill/context
and file authorities, Sandbox policy, callback receipt semantics, and public
SSE contract. A future adapter remains a proposal until its registry, scope,
conformance tests, packaging, and runtime evidence exist.
