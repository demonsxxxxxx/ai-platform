# Current RAGFlow Surface Disposition Contract

## 1. Reviewed source baseline

This migration inventory is bound to repository commit
`55845923ba608d3c0d4e747fc6571fd5168d4d13`. A cleanup Issue must refresh the
inventory against its exact base and add any newly discovered producer,
consumer, persisted identity or public contract before deleting code.

The current repository does not contain a Knowledge Source, dataset catalog,
retrieval evidence or citation domain. Its RAGFlow behavior is a seeded
Skill/MCP/Agent demonstration path plus compatibility and redaction code. The
new product therefore migrates explicit persisted identities and consumers; it
does not rename that path and treat it as the Knowledge authority.

## 2. Required disposition fields

Every row below is a migration contract. `Retire` means remove the producer
after its gate passes. `Compatibility reader` means preserve bounded historical
read behavior without permitting new writes. `Retain` means the behavior is a
valid safety or regression boundary, not product authority.

| Item | Current fact and persisted identity | Required disposition | Deletion gate | Rollback boundary |
| --- | --- | --- | --- | --- |
| Schema-seeded Skill | `app/schema.sql:2931-2958` writes Skill `ragflow-knowledge-search`, version `0.1.0`, version ID `skv_seed_ragflow_knowledge_search_0_1_0`, and default distribution. | Stop the seed writer after the canonical Knowledge release exists. Mark the Skill unavailable for new Agent revisions; preserve immutable historical Skill/version rows. | Count Agent revisions, Run snapshots and queued/retryable Runs that reference the Skill; prove new writes are zero. | Restore availability only for a named still-supported historical execution contract; do not restore it as Knowledge authority. |
| Schema-seeded MCP tool and policy | `app/schema.sql:2960-2992` writes tool `ragflow-knowledge-search`, server `ragflow`, remote tool `ragflow_search`, and its default policy. | Retire the built-in MCP registration and policy writer after deterministic Knowledge retrieval is accepted. Generic MCP catalog behavior remains. | No published Agent revision or admitted Run selects the tool; no external client depends on its admin/catalog identity. | A bounded read-only compatibility row may be restored for a named consumer; Knowledge writes remain separate. |
| Schema-seeded Agent | `app/schema.sql:2994-3011` writes Agent `sop-assistant` with default Skill `ragflow-knowledge-search`. | Require an administrator to create or revise an Agent App with explicit logical Knowledge Source bindings. Hide the seeded Agent from new Market discovery after that replacement is published. | Inventory published revisions and conversations; record the replacement Agent/revision and deep-link decision. Dataset binding cannot be inferred automatically. | Keep historical Agent/message labels readable; do not silently rebind old conversations to a new revision. |
| Skill asset | `skills/ragflow-knowledge-search/SKILL.md` describes RAGFlow retrieval as a Skill-backed MCP operation. | Retire it from the active Skill catalog because Knowledge binding is an Agent resource. Preserve any immutable artifact required to interpret retained snapshots. | No active/retryable Run can materialize the asset and no published revision selects it. | Restore the exact archived asset only for a proven historical materialization need. |
| Capability registry | `app/capabilities.py:52-60` maps `knowledge_answer` to `sop-assistant` and `ragflow-knowledge-search`. | Replace the hard-coded product mapping with the safe Knowledge capability projection of an Agent revision, then retire this entry. | Market, Builder, Chat and history contracts no longer read this static mapping. | Preserve the public label through the replacement projection, not by recreating the old writer. |
| Intent and Chat special case | `app/intent_router.py:172-180,255-265` selects the static `knowledge_answer` capability; `app/routes/chat.py:1200-1209` selects or defaults the seeded Agent/Skill identity. | Delete provider- and identity-specific routing after Agent-detail entry and Run admission derive Knowledge only from the pinned Agent revision. | Route/intent consumer inventory is complete; deep-link and copied/retried Run tests use the canonical Agent path. | A compatibility request translator may map a proven public alias to an Agent ID; it cannot select a Skill, provider or dataset. |
| Public/internal identity translation | `app/projection_redaction.py:16-49` translates `sop-assistant`, `ragflow-knowledge-search`, `knowledge_answer` and `knowledge-answer`. | Replace active selection with canonical Agent projection. Retain only bounded historical alias/redaction readers required by stored messages or public links. | Exact persisted/public alias inventory and a reader/writer matrix prove which branches are no longer reachable. | Restore a read translator only; never restore a second selection writer. |
| Historical context redaction | `app/routes/context.py:50-58` treats the seeded Agent and Skill IDs as forbidden memory-preview markers. | Retain while any retained context or message can contain these values; reassess only with a complete retention proof. | Absence proof across retained context/message payloads, not source grep alone. | Re-add the markers without changing runtime authority. |
| Generic public payload redaction | `app/platform/public_payload.py:84` removes `ragflow_payload`. | Retain as a defense-in-depth compatibility sanitizer until the field is structurally impossible in all retained/public payloads. | Generated-contract and retained-payload proof show no producer or stored value. | Re-add the sanitizer; it owns no business write. |
| MCP trusted-builtin branches | `app/mcp/repository.py:21-75,156` grants a code-owned special case to the seeded tool; `app/repositories.py:3528-3558` joins it into capability projection. | Retire only the RAGFlow special case. Keep provider-neutral MCP registration, authorization and catalog APIs. | Seed/tool/policy migration is complete and no admitted Run requires built-in provenance. | Restore a bounded MCP compatibility reader for a named row; do not route Knowledge through it. |
| Skill catalog and replay branches | `app/repositories.py:1235-1246` and `app/skills/dependencies.py:6-12` expose the seeded Skill through the public catalog; `app/skills/infrastructure/postgres.py:20-23,222-236` defines and validates its trusted MCP replay identity. | Remove it from active Skill selection after publication migration; retain historical snapshot interpretation until its reference gate passes. | Exact Agent revision, Skill release, Run and replay reference counts are zero for active/retryable subjects. | Restore historical replay interpretation only. |
| Direct executor rejection | `app/worker.py:2393-2405` explicitly rejects historical `executor_type=ragflow`; the default adapter registry has no RAGFlow executor. | Preserve fail-closed behavior during migration. It may later become a provider-neutral unknown-executor rule after persisted/queued direct-RAGFlow runs are absent. | Queue, Run payload and retry/resume inventories show no `executor_type=ragflow`. | Restore fail-closed rejection, never a direct RAGFlow executor. |
| Historical terminal error projection | `app/runs/domain/public_terminal.py:99` maps `ragflow_api_error` to a safe public class. | Keep as a compatibility reader while retained events may contain the code; new Knowledge errors use the typed Knowledge taxonomy. | Retained event inventory proves the old code is absent or outside supported reads. | Re-add the mapping without adding a producer. |
| Deployment comment | `deploy/ai-platform/.env.example:111` contains one empty `Optional RAGFlow knowledge executor config` comment and no corresponding settings. | Delete the obsolete comment with the first cleanup slice. | Confirm no packaging script parses the comment. | Documentation-only restore. |
| Retired-setting regression | `tests/test_settings.py:238-253` asserts `ragflow_api_url`, `ragflow_api_key`, `ragflow_default_dataset_id`, timeout, top-k and threshold are absent from Settings. | Retain and extend so those legacy global settings cannot reappear; new configuration belongs to versioned Knowledge Connections. | No deletion is planned while the legacy names remain a plausible regression. | Not applicable; this is a negative configuration contract. |
| External SOP launchpad entry | `frontend/web/src/components/launchpad/**` and browser runtime-config tests use `sop_assistant` as an environment-owned external URL key. | Retain unchanged under the platform runtime-config/launchpad owner. The configured target URL remains environment-owned, is not a Knowledge API, and is outside this migration. | No deletion gate applies in this product; a separate launchpad Issue with a named business owner is required to change or remove the key. | Restore the external link only, not the seeded internal Agent/Skill path. |
| Tests and readiness selectors | Backend tests and frontend event/history test fixtures name the seeded identities; no production frontend reducer contains those constants. `app/foundation_alpha_readiness.py:1130` selects a RAGFlow tool-call test. | Move each test with its production owner: delete tests for retired writers, keep redaction/history fixtures for compatibility readers, and replace runtime tests with Knowledge contracts. | Every production row above has an owning replacement or retained-reader test; readiness references only runnable current behavior. | Restore the exact owning test with any restored compatibility reader. |

## 3. Provider-independent surfaces that remain

Cleanup must not remove the generic MCP catalog or authorization model, the
generic Skill release/materialization model, immutable Agent Profile revisions,
Run admission, Conversations, terminal hydrate, SSE v4, identity directories,
department/role selectors, or shared redaction helpers. These are platform
authorities consumed by the new product and are not part of the seeded RAGFlow
demonstration path.

## 4. Cleanup proof order

1. Freeze the exact cleanup base and refresh this inventory.
2. Query persisted Skill, version, Agent, MCP, policy, Agent revision, Run,
   message and event identities.
3. Identify external clients, launchpad links, bookmarked public aliases and
   operational selectors.
4. Merge and accept the canonical replacement before retiring an active writer.
5. Stop the old writer; preserve required compatibility readers.
6. Prove new writes are zero over the declared observation window.
7. Remove one production branch and its owning tests per cleanup change.
8. Run post-delete source absence, persisted-reader and external acceptance
   checks before removing the next compatibility reader.
