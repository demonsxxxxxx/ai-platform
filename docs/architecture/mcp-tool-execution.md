# MCP tool execution repair

Status: active implementation contract and acceptance checklist. Source and local checks do
not establish production deployment or external acceptance.

## Scope and ownership

Repair the path from Server registration and user-effective discovery through
Chat/Profile references, Worker authorization, Sandbox transport, Claude SDK
registration, tool calls, and public events. MCP owns catalog/reference and
remote transport constraints; Execution owns the Claude adapter; bootstrap
assembles concrete adapters. Existing Run/Attempt/lease and callback authorities
remain unchanged. No new host relay, service, database catalog, or dependency.

The current installed baseline is claude-agent-sdk 0.2.130, bundled Claude Code
2.1.222. Official contracts: [MCP](https://code.claude.com/docs/en/agent-sdk/mcp),
[permissions](https://code.claude.com/docs/en/agent-sdk/permissions),
[hooks](https://code.claude.com/docs/en/agent-sdk/hooks), and
[Python API](https://code.claude.com/docs/en/agent-sdk/python).
`allowed_tools` grants automatic approval; it does not filter the tool catalog.
Mandatory per-call checks stay in PreToolUse, including when can_use_tool is
skipped by SDK approval rules. The installed CLI normalizes server/tool names
by replacing characters outside `[a-zA-Z0-9_-]` with `_`.

## Principal credential lifetime

The principal JWT store holds one encrypted company credential per tenant/user;
its key and authenticated envelope preserve that binding. `put` sets Redis TTL
from the credential expiration. `get` validates the captured credential and
returns the existing expired-credential error when its deadline has passed.
Redis TTL owns reclamation: an expired read must not delete the shared key,
because a concurrent login may already have replaced it with a valid credential.
This does not change browser authentication lifetime, logout, or the existing
last-write-wins credential replacement policy.

## Implementation

1. Keep `server::raw_tool` references and canonical platform subject identities
   unchanged. Build an explicit canonical-to-SDK alias map in the Claude
   adapter; use stable bounded hash suffixes to disambiguate normalized names.
   Resolve incoming hook/message/denial names consistently, without guessing
   that underscores mean dots. Sanitize aliases as private runtime identities.
2. Before model execution, open the selected external Servers using the
   installed MCP client, current static headers and `JWT-Authorization`.
   Discover all pages with bounded time/size/cursor/name validation. Missing
   selected tools or failed connections remove only those optional capabilities.
   Servers discover concurrently; discovery timeouts end before the task waits
   for connection release.
   Expose only selected Tool definitions using the SDK's in-process MCP Server
   support; preserve model-visible input schemas, annotations, result content,
   structured result data and `isError`. Remote `outputSchema` remains on the
   typed Tool at the MCP boundary; SDK 0.2.130 does not forward it to the model.
   Because that bridge also drops the optional
   `structuredContent` field, the adapter carries it as a bounded JSON text
   content item while retaining the typed field for direct MCP callers.
   Forward calls to the original remote names. Do not expose
   unselected siblings or use a Server-wide wildcard permission grant.
3. Each discovery task owns and closes its remote session. Close SDK query streams,
   sessions, transports and tasks on success, failure, timeout and cancellation.
   Use `strict_mcp_config=True` so project/user/plugin MCP config cannot expand
   the Server set. Selection remains optional use: a connected selected tool
   need not be called, but an observed call still requires existing evidence.
   `PreToolUse` may arrive before the corresponding assistant `ToolUseBlock`; the
   hook's validated call ID, tool name and private input seed the same per-call
   state so event publication order cannot turn an authorized call into a false
   denial. Platform-owned response and context Tool subjects supply fixed safe
   `public_tool_label` metadata so their policy and lifecycle events can be
   acknowledged before dispatch. These labels exclude private identities,
   arguments, runtime paths and results; the existing event acknowledgement and
   execution-evidence checks remain required. Known failed calls to platform-authorized read-only tools may recover.
   A complete remote `CallToolResult(isError=true)` may also end a potentially
   write-capable call as a known failure when the adapter uniquely matches the
   canonical tool and original argument digest to an admitted native call ID,
   and that call's failure hook and receipts are acknowledged. The private
   evidence retains `lifecycle_phase=failed` with
   `trust_basis=tool_call_bound_error_result`; it never claims tool success or
   absence of partial writes. Other authorized calls may continue. Error text,
   model claims and remote read-only hints do not supply this evidence. Identical
   concurrent calls whose response cannot be uniquely joined remain uncertain.
   For write-capable calls after admission, a missing terminal hook is
   `mcp_execution_outcome_unknown`; a completed hook whose durable callback or
   public receipt is incomplete is `mcp_execution_succeeded_receipt_incomplete`.
   Both require reconciliation and are not retryable. SDK error messages,
   provider-session failures, exceptions, timeouts and the outer Executor
   deadline preserve that uncertainty after an observed MCP invocation.
   Retry and resume reject either error code at the locked Runs control
   boundary and in the public readiness/manifest projections.
   A failed external write without a uniquely bound returned-error receipt,
   identity conflict or unacknowledged receipt also denies subsequent external
   writes in the current SDK turn. Admission rechecks
   after awaited receipts to cover concurrent failures. Ordinary concurrent
   writes remain available before an uncertain outcome is observed; authorized
   read-only tools remain available for reconciliation when their receipts work.
4. Support Streamable HTTP and legacy SSE explicitly in discovery and execution.
   Preserve endpoint validation, DNS pinning, same-origin SSE message endpoints,
   redirect rejection, reserved-header protection, and bounded responses.
   Both discovery transports and selected execution use the installed official
   MCP `ClientSession` through `mcp.infrastructure.client`; the platform does not
   implement a second JSON-RPC handshake, response parser or session protocol.
   SDK-supported protocol negotiation, request IDs and SSE framing stay SDK-owned.
   Platform-owned strict result validation rejects coercible annotation values
   (for example a string or number standing in for `readOnlyHint`), while valid
   optional and extension annotations remain supported. SDK wire errors and
   response guard failures fail closed, including errors on optional background
   streams; they cannot authorize a retry to another destination or accept a
   later catalog result after malformed input.
   Discovery applies one total deadline across DNS resolution, initialization,
   all catalog pages and local cleanup, in addition to per-request and page limits.
   HTTP discovery retains the whole-response byte limit, including SSE responses;
   long-lived SSE sessions retain per-event limits. The injected HTTP client pins
   every SDK request, including optional Streamable HTTP GETs, to the validated
   address and original Host/SNI. A server may reject the optional GET with 405.
   Discovery closes local resources without sending remote session DELETE;
   selected execution retains its existing SDK session-termination behavior.
   Deadline expiry retains the safe transport-failure category; caller cancellation
   still propagates and closes the owned client/response.
   New or updated `sandbox`/command Server configurations are rejected: the
   platform has no governed stdio implementation. Existing registry rows remain
   readable/deletable and can be migrated to HTTP/SSE with an explicit endpoint;
   no stored data is deleted. This is unsupported-capability retirement, not
   permission to run arbitrary commands in the API or Worker.
5. Use the MCP reference validator for Sandbox `mcp_tool_ids`, detect duplicate
   names within a page as well as across pages, and preserve discovery failure
   state in the ordinary-user MCP directory. The live catalog retains a private,
   payload-free failure (allowlisted reason, fixed exception category and the
   target-resolution, transport-selection, discovery or projection stage).
   Both discovery routes emit it once through their existing logger with tenant,
   Server and a fresh catalog-request trace ID; concurrent Server failures share
   that trace. Unknown errors retain only `unexpected_error` and a fixed class
   category. No exception text, traceback, endpoint, header, JWT or tool input is
   included. Ordinary per-Server responses keep `discovery_failed` (or the
   existing `unsupported_transport`) and never serialize the private diagnostic.
   Bulk JWT lookup maps only missing, expired or invalid principal credentials
   to `authorization_required`; operational/configuration failures return
   `discovery_failed` and retain their typed reason in the private log. The single
   Server route keeps its typed HTTP error and existing force-relogin header
   rules. Cancellation propagates without an unavailable result or failure log.

## Runnable checks

Run these from the repository root through the governed local stage runner:

```text
python tools/run_test_stage.py --stage mcp-discovery-regression --timeout-seconds 60 -- tests/test_mcp_tool_catalog.py tests/test_mcp_client.py
python tools/run_test_stage.py --stage mcp-boundary-acceptance --timeout-seconds 60 -- tests/test_mcp_client.py tests/test_claude_mcp_registration.py tests/test_claude_agent_events.py tests/test_claude_agent_sdk_installed_contract.py
python tools/run_test_stage.py --stage mcp-runner-regression --timeout-seconds 60 -- tests/test_claude_agent_sdk_runner.py
python tools/run_test_stage.py --stage mcp-owner-regression --timeout-seconds 60 -- tests/test_claude_agent_worker_adapter.py::test_sandbox_sdk_options_and_hooks_use_exact_authorized_capability_subjects tests/test_claude_agent_worker_adapter.py::test_external_mcp_available_or_exactly_invoked_succeeds_in_sandbox tests/test_sandbox_executor_app.py::test_executor_binds_sdk_mcp_evidence_and_emits_only_safe_capability_event tests/test_sandbox_executor_app.py::test_executor_rejects_unknown_capability_identity_without_inference
```

The stage runner removes inherited `PYTEST_*` variables, creates a unique local
basetemp, emits per-test progress and writes JUnit plus evidence output. Add the
route/catalog/reference selectors from the existing MCP suite when changing
those boundaries. Frontend checks use the installed `tsx` test runner, for
example:

```text
cd frontend/web && node node_modules/tsx/dist/cli.mjs --test src/components/mcp/__tests__/MCPServerForm.test.tsx src/components/panels/__tests__/OrdinaryMcpCatalog.test.ts src/components/panels/__tests__/governanceSurfaceSource.test.ts
```

## Invariants and stop conditions

- Worker remains the authority for current principal, tenant, Server distribution
  and selected tools. The adapter cannot mint subjects or widen selection.
- Credentials remain process-private; no credential values, URLs, raw runtime
  aliases or private tool arguments enter public events or persisted receipts.
- Preserve parameter schemas and external error results, and keep call IDs,
  canonical identities, Attempt and lease bindings in existing callback checks.
- Never turn missing discovery, ambiguous names, denied calls or failed tests
  into success through wildcard authorization, retries, skips or stale caches.
- Stop dependent work if safe transport, exact selection or deterministic
  cleanup cannot be proved. Do not deploy or claim production acceptance from
  local synthetic services. Commit/push/deployment require separate authority.

## Verification and acceptance

Use `python tools/run_test_stage.py --stage NAME --timeout-seconds N -- SELECTORS`
from the worktree; use its explicit per-file/node selectors, basetemp, timeout,
JUnit and evidence output. Frontend uses its installed tsx test runner.

| Requirement | Runnable owning regression / acceptance |
| --- | --- |
| SDK naming | Dotted/colon names, punctuation in Server names, underscore names, alias and Server collisions; exact canonical policy and completion receipts |
| Selection | Real bundled CLI against synthetic localhost MCP/model endpoints sees selected input schemas only; remote receives original tool name/arguments; unknown calls never reach remote |
| Results | Text, structured content and remote isError preserved; SDK failure and callback failure cannot produce a successful tool receipt |
| Transport | Real local HTTP and SSE handshake/list/call; JWT/static headers on requests; same-origin message endpoint; no redirect, DNS rebinding or forbidden endpoint dispatch |
| Lifecycle | Discovery failure/missing tool removes the unavailable optional capability; normal completion, exception and cancellation close owned sessions and tasks |
| Configuration | strict MCP config set; HTTP/SSE accepted; sandbox/command writes rejected and existing records preserved |
| References/catalog | Long valid MCP references accepted at Sandbox boundary; malformed references rejected; same-page and cross-page duplicate names/cursor loops rejected |
| UI | HTTP-200 discovery failure renders unavailable, not empty; stale response generation protection remains |
| Existing behavior | MCP route/repository/catalog/capability suites and directly affected Runner, Sandbox and event tests |
| Architecture/docs | Source authority/document checks and applicable architecture inventory; no frozen hot-file growth or cross-context internals imports |

Real CLI acceptance must use synthetic credentials and local services only, no
billable model or production MCP. Record exact commands/results and limitations
in the active delivery report and generated test evidence, not a second durable
status ledger. External acceptance after authorized release additionally checks
an ordinary user's selected real tool in Chat and Profile execution, visible
progress and result, unselected-tool exclusion, service-unavailable behavior,
and absence of secrets in public events. That stage remains unclaimed until run.

## Retirement and compatibility disposition

- Replace whole-remote-Server SDK registration for selected external tools with
  an Executor-local selected tool surface; no parallel direct-registration path.
- Retire raw platform names in SDK allow rules and exact raw-name lookup at the
  incoming SDK boundary. Canonical persisted/public identities remain compatible.
- Replace HTTP-only discovery dispatch with explicit transport dispatch; keep
  supported Streamable HTTP behavior and its endpoint controls.
- Retire manual HTTP discovery initialization, JSON-RPC extraction, SSE `data:`
  parsing and separate page walker. Both bootstrap discovery entries now use the
  client-owned adapter and the same guarded SDK transport as execution. Remove
  the unused hash-only discovery projection and endpoint-only wrapper; no
  compatibility aliases remain. Synthetic wire tests exercise the real SDK
  initialize/list-tools path, strict annotations, byte limits, credentials,
  pagination and cancellation instead of accepting incomplete RPC envelopes.
- Reject unsupported command/Sandbox writes and remove command-entry UI. Retain
  existing rows for administrative inspection and explicit transport migration.
- Replace tests that assume selected tools are raw remote SDK configs with
  selected-surface and actual SDK assertions; retain policy and receipt tests.
- Replace generic completion-evidence mismatch after an admitted MCP call with
  explicit succeeded-but-receipt-incomplete or outcome-unknown errors. Retry
  and resume readiness and child creation reject both; pre-admission mismatches
  remain compatible and no parallel retry path remains. The installed CLI MCP
  regression now runs in the Sandbox CI shard; its selected catalog also
  includes the platform-owned `attach_file` tool.
- Replace the catalog's unobserved catch and the bulk-only fallback logger with
  one private failure projection consumed by both discovery routes. Retire the
  blanket JWT-error-to-authorization mapping; the bulk response shape and known
  authentication reasons remain compatible. `McpToolDiscoveryError` now belongs
  to MCP domain errors and all explicit import consumers use that owner; the
  transport/parser still raise the same type and reason. No database, credential
  format, transport behavior or public diagnostic field is added.
- Replace unconditional uncertainty for an admitted write-capable MCP's failed
  hook with call-bound proof of a complete returned error result. Unproven failed
  hooks retain the existing uncertainty path; no name/error-text whitelist or
  parallel execution path remains. Existing v1 evidence fields are unchanged;
  the added MCP-only failed-result trust basis is consumed by SDK, Sandbox and
  Worker together. Legacy hook evidence remains conservative. The Runner,
  registration, shared evidence, Sandbox binding and Worker selectors cover
  failure recovery, ambiguous joins, missing callbacks and transport exceptions;
  the installed CLI selector now exercises possibly writable remote error results.
- Platform-owned response/context public labels complete existing Tool subjects;
  no production path, canonical identity or wire schema is superseded.
- Update the owning MCP section in `docs/frontend/skills-marketplace-public-api.md`
  and link this execution contract there. Inventory affected selectors and
  configuration references with targeted searches before completion.


## Selection policy ownership

Chat selected/available admission and source-identity replay share the MCP
application selection use case. Bootstrap only composes the PostgreSQL registry
lookup and Identity distribution adapters. Runs consumers use MCP's public API;
the Server-qualified lookup SQL has one owner in `infrastructure/postgres.py`.
The retired legacy catalog list and duplicate Chat access repository policy are
not alternative admission paths. Retained builtin tool-policy SQL remains used
by its existing persistence adapter.

Replay still checks source-persisted roles/department with empty permissions,
then queue preparation checks the current effective principal; Worker dispatch
still independently checks its current authority. These boundaries must not be
collapsed. Selection normalizes roles as before, preserves explicit `is_admin`
and permissions, and never recomputes admin status from roles.

Compatibility wrappers preserve existing errors: corrupt distribution scope
remains a conflict for Chat selected/available (409) and an authorization denial
for source replay (403). Available mode filters ordinary unavailable tools, but
does not swallow corrupt authority. Duplicate-selection audit identity remains
`mcp_tool` for Chat and the duplicated tool reference for source replay.
