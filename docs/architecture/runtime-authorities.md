# Runtime Authority Map

AI Platform is an internal enterprise Agent (Harness) application centered on
governed Skills. Platform code owns identity, authorization, release policy,
persistence, context snapshots, file and artifact facts, run admission, and
public projections. A selected Harness SDK owns only the model and tool loop
behind an adapter.

## Authorities

| Capability | Business authority | Allowed adapters and projections |
| --- | --- | --- |
| Agent definition | Immutable `agent_profiles` revisions and publication lifecycle | Public/admin profile projections |
| Skill release | Admin Skill review, materialization, promote, and rollback lifecycle | Marketplace read and tenant distribution routes |
| Harness execution | The registered Harness adapter selected by run admission | Claude Agent SDK today; a future Pi adapter must implement the same platform contract |
| MCP and external knowledge | Governed MCP server and tool catalog | RAGFlow is an MCP/tool capability, never an independent chat executor |
| Streaming | `app.streaming` event replay, cursor, heartbeat, and terminal contract | Chat and compatibility routes only translate the shared stream |
| Context | Pinned platform context snapshot and governed memory selection | Engine adapters receive the snapshot; they do not rebuild platform context |
| Files and artifacts | Platform file/artifact records and authenticated download contract | Sandbox staging and SDK upload helpers only transfer bytes for an authorized run |
| Sandbox runtime | `SandboxRuntime` control authority and its durable run-attempt binding | Docker/OpenSandbox provider translation is allowed; provider SDK state is not a business lifecycle authority |

Base Harness chat and specialized Skills are separate execution identities. A
base chat run carries `execution_kind=harness_chat`, no `skill_id`, and an empty
Skill authority. A specialized run carries `execution_kind=skill` plus an exact
authorized Skill/version/release snapshot. A Harness implementation is not
modeled as a synthetic default Skill.

## Compatibility rule

A compatibility module may normalize an import, request, response, or persisted
historical event only when it delegates to the authority above and performs no
independent write, admission, release, or execution decision. Retired inputs
must fail closed through the current authority. Historical read projections may
remain while persisted records still exist, but no retired worker, dispatcher,
or executor may produce new facts.

## Harness replacement rule

Replacing Claude Agent SDK with another Harness such as Pi changes the Engine
adapter and its private SDK event translation. It must not change route
contracts, repository schemas, Skill release decisions, context/file
authorities, sandbox policy, or the public SSE event contract.
