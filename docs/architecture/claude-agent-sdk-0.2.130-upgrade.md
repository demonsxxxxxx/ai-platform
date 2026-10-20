# Claude Agent SDK 0.2.130 And Runtime Capacity Profile

Status: historical upgrade and decision-baseline record. Version and capacity
numbers below describe that upgrade, not the currently deployed profile.
Current dependency authority is `pyproject.toml` plus `uv.lock`; current resource
limits come from settings and effective deployment configuration. See
[packaging](docker-packaging.md) and [system architecture](system-architecture.md).
Preserve the public-projection failure contract below until its current owning
Runs/SSE/Chat contracts explicitly replace it. No dependency is changed here.

The adapter mapping below reflects the current ordinary-text and optional
`attach_file` contract. Earlier structured-output-only descriptions are retired;
the upgrade history does not require every conversation to return JSON. The
[streaming message design](../implementation/streaming-message-parts-design.md)
records the current v4 projection and its compatibility boundary.

## Historical decision

Pin `claude-agent-sdk==0.2.130`, set the process worker profile and global
worker-run admission ceiling to 10, and bound each API or worker process to 10
Redis connections through one shared client per event loop. The per-user active
run limit remains 3 and the database pool maximum remains 10.

This is a source and container-configuration profile. It is not evidence that a
deployed environment can sustain 10 simultaneous runs. Docker image validation
and controlled-host capacity acceptance remain external gates.

## Official Upgrade Evidence

Before the first implementation edit on 2026-08-04, the PyPI JSON API and the
official Anthropic GitHub release both identified stable version `0.2.129`.
After merge-up and reauthorization on 2026-08-05, both official sources had
advanced to stable version `0.2.130`; its six PyPI artifacts were not yanked and
the GitHub release was neither draft nor prerelease. The upgrade starts from the
repository's former exact pin, `0.2.87`.

Official releases from `0.2.88` through `0.2.130` and the target tag source were
reviewed. Changes relevant to this adapter include AnyIO/Trio session storage,
MCP dependency compatibility, `TaskUpdatedMessage`, subprocess cleanup during
cancellation, NDJSON and malformed-content handling, resume/session argument
fixes, Windows command hardening, `ResultMessage.terminal_reason` and typed model
usage, background-task stdin lifetime, and strict Skill name/`allowedTools`
validation in `0.2.129`.

Version `0.2.130` changes only package metadata and the bundled Claude CLI from
`2.1.221` to `2.1.222`; it does not change the Python SDK symbols or option
types used by this adapter.

## Adapter API Difference Record

| Surface | 0.2.130 contract | Platform handling |
| --- | --- | --- |
| `query` | Keyword `prompt`, `options`, and optional `transport` remain available | The async iterator stays inside the runner adapter |
| `ClaudeAgentOptions` | Existing model, system prompt, tools, hooks, session, limits, and stream fields remain available | Constructed only after platform admission and Skill-name validation |
| `HookMatcher` | `matcher`, `hooks`, and `timeout` remain available | Exact `PostToolUse` evidence remains the only Skill-success authority |
| Messages | `AssistantMessage` carries independently completed blocks; several observations may share a provider message; child scope uses `parent_tool_use_id` | Only eligible main `TextBlock` content enters the public gate; Thinking, child text, raw prose and private errors are excluded; exact UUID replay is suppressed |
| Terminal result | `ResultMessage` adds `terminal_reason` while retaining result/error/session/usage fields | Result controls SDK completion, error, session and usage, never public body repair; committed public text and receipt own the answer; Runs owns business outcome; files are selected separately |
| Partial streaming | SDK supports `include_partial_messages=True`; the Claude adapter selects `False` | Completed main `AssistantMessage` blocks feed the existing public gate; raw/Result bodies have no public text authority. Provider-bound classification groups work without rewriting accepted rows |
| Settings | `setting_sources` remains supported | Only explicit project settings are loaded after platform-controlled scrubbing |
| Permissions | `permission_mode`, allowed tools, disallowed tools, and `can_use_tool` remain supported | Platform authorization, admission, sandbox, and context remain authoritative |
| Limits | `max_turns`, `effort`, and `max_thinking_tokens` remain supported | Max-turn termination maps to a stable public platform error |
| Automatic compaction | Bundled CLI `2.1.222` owns ongoing auto-compaction and accepts numeric windows from 100k through 1M | The runner clamps the immutable Run input capacity to that CLI range and does not issue `/compact` when opening or resuming a session |
| Process context | `cwd` and `env` remain supported | The runner supplies the governed workspace and an allowlisted environment |
| Abort/cancel | `query` has no explicit interrupt method; task cancellation closes iterator/subprocess work | Outer cancellation propagates; SDK abort terminal reasons map to cancellation |

The target wheel is also exercised in an isolated local environment without a
model or network call. That smoke imports the installed distribution, checks
metadata and signatures, constructs every option and hook shape used here, and
instantiates the stream and terminal message types.

## Terminal result assembly

The runner has one terminal snapshot assembler for session, usage, Skill and
capability observations, diagnostics, and deliverable fields. Each terminal
branch chooses its error and diagnostic source before assembly; failures close
the answer gate and seal candidates before diagnostics are captured. Successful
results expose files and provider sequence only after terminal validation.
Cancellation still propagates after the consumer task is stopped and awaited.

## Result usage and estimated cost

Execution's Claude harness observes each `ResultMessage` before background-task
or supplemental-input continuation can hide it. The SDK's top-level
`total_cost_usd` is not a member of its `.usage` dict. The private platform
`sdk_usage` carrier is a normalized projection: existing per-turn main-loop
token aggregation is retained, while the official `total_cost_usd` and
`model_usage` fields are latest cumulative snapshots, never recursively added
across turns. Per-model rows keep only bounded numeric token/cost fields and
bounded private model keys. They are not exposed in public Run results/events.

The historical upgrade used SDK 0.2.130 / CLI 2.1.222. The current pin is SDK
0.2.165 / CLI 2.1.294; [file-task composition and current native resume](claude-provider-session-continuity.md#file-task-prompt-composition-sdk-02165)
own that transition. The [official cost contract](https://code.claude.com/docs/en/agent-sdk/cost-tracking)
distinguishes turn usage from cumulative snapshots and documents changed resume
accounting in CLI 2.1.277. An isolated two-process 0.2.165 external-SessionStore
probe returned the synthetic provider's 42 input / 4 output tokens and USD
0.000186 separately for each process, not an accumulated 84 / 8 on resume.
Those fixture numbers qualify scope only, not real usage or billing. Revalidate
that boundary when changing the SDK or store path. SDK prices are client-side
estimates, not authoritative billing data.

The single private `cost_coverage` marker is `complete`, `partial`, or
`unavailable`. Missing/invalid latest cost does not erase an earlier observation.
An execution-crash result can contain false zeroes; earlier observations remain
partial. Missing final results, cumulative regressions, changed session identity,
or initial/supplemental `/clear`, `/reset`, or `/new` commands never manufacture
a complete Run total. The adapter observes explicit reset inputs and result identity rather than
reconstructing accounting across resets. It does not alter user
input or reconstruct accounting across resets. A legitimate zero on an intact
call remains a complete zero. Optional telemetry failure cannot fail a Run.
Cancellation still propagates directly without assembling a returned result;
this observer does not persist a partial cost on that cancellation path.
Partial snapshots are carried only by results the existing assembler returns,
including missing-final-result EOF and transport failures.

When the whole call has no normal `.usage` observation, a complete final
`model_usage` snapshot may supply the fallback token projection once. Its
whole-tree scope is not added to existing main-loop counters. Mixed coverage
does not silently combine those scopes. Known nonnegative finite complete USD
estimates retain the existing `Decimal` half-up conversion to hundredths;
invalid, incomplete or out-of-range amounts do not enter that numeric metric.

Retirement: remove the runner's `usage or model_usage` shape substitution and
its implied cumulative addition. Historical carriers without `cost_coverage`
retain their existing cost aliases at the reader only. New production writes
use official source field names. The existing non-null integer DB column remains a compatibility carrier.
New normalized `result_json.cost.estimated_cost_minor` observations preserve
unknown as explicit JSON null and complete zero as zero. Only new observations
carry the bounded `runtime_private_payload.sdk_usage.cost_coverage` enum. This
existing private container is excluded by the public sanitizer; no raw SDK
usage, model identity, prior partial amount, or public coverage field is added.
Readers use a marked complete, bounded integer observation (including zero)
before the compatibility column; marked partial/unavailable or malformed complete
observations are unknown. Unmarked legacy rows, including conflicting old JSON
amounts, retain their column reader and original event bounds. No rows or monetary
write/accumulation rules are rewritten. Mixed aggregate coverage is unknown,
not an apparently complete subtotal; an empty aggregate retains its zero.
Terminal event payloads preserve the same small private enum wrapper and
normalized cost, then omit the optional public cost envelope when unknown;
token counts remain independent. Cancelled results with a new marker copy only normalized
latency/token/cost observations and the enum wrapper, never the discarded answer
or private executor content. Unmarked cancellation keeps its message-only contract. Small known amounts retain existing rounding.
No billing account, exchange-rate service or second cost ledger is introduced.

Owning regressions exercise real installed SDK result objects, same-client
continuations, cumulative replacement, model-only fallback, unknown/zero,
crash/reset/absence and invalid-number boundaries, existing terminal metrics,
and exclusion of private carrier fields from public projections. They extend
existing canonical backend test files, without a separate workflow.

## SDK-native automatic compaction

Execution passes the immutable Run `max_input_tokens` through
`ClaudeAgentOptions.extra_args["autocompact"]`, clamped to Claude Code's public
100k-1M numeric range. Claude Code owns its output reserve and safety buffer, so
the platform does not apply an additional percentage reduction and the actual
compaction point remains earlier than the configured window.

`CLAUDE_CODE_MAX_OUTPUT_TOKENS` continues to carry the independent output
ceiling. The inherited `CLAUDE_CODE_MAX_CONTEXT_TOKENS` remains scrubbed because
the platform does not own a separate raw total-context value.

This replaces the runner-authored resume preflight that inspected context usage
and issued `/compact` before the business query. That preflight, its private
permission-mode branch, `context_native_compact_failed`, and the bootstrap-only
413 response remain retired. The model proxy no longer calls
`/v1/messages/count_tokens` before forwarding each Claude `/v1/messages` request
or synthesizes an input-limit response; Claude Code and the upstream model own
that input-window handling. An explicit `/v1/messages/count_tokens` request made
by Claude Code still traverses the Run/Attempt-bound credential proxy and keeps
its existing validation and failure behavior. HTTP request-body size limits,
output-capacity validation, and their existing errors are unchanged.

## Change Contract: public answer projection failures

- **Owner:** Execution owns Claude free-text sanitization, public-event candidate
  construction, and SDK result assembly. Runs still owns Run terminal state, and
  Artifact storage still owns file collection, integrity, and delivery.
- **Bounded paths:** `app/platform/public_payload.py`,
  `app/executors/public_answer_stream.py`,
  `app/execution/application/claude_agent_events.py`,
  `app/executors/claude_agent_sdk_runner.py`, owning runner/Worker/sandbox tests,
  and this contract. SSE v4, Runs terminal, Artifact storage, Tool/Skill
  admission, and frontend state contracts are unchanged.
- **Invariants:** ordinary answer text may contain Unix/Windows
  paths, filenames, code, and technical identifiers. Model Thinking content is
  not part of the public answer projection. Credentials, configured
  model credentials/base URLs, MCP static header values, native tool tokens,
  and exact run-bound private values remain locally redacted; structured event
  fields, storage keys, executor payloads, and private event identities remain
  strict.
- **Event atomicity:** candidate validation completes before deduplication,
  answer-started state, delta counts, text length, last-delta identity, or
  receipt state advances. A rejected candidate leaves no partial commit.
- **Delivered-text authority:** `message.completed`, the terminal answer, and the
  answer receipt describe only callback-acknowledged text. Raw terminal text
  cannot replay an omitted fragment or override text already delivered.
- **Fault separation:** a local answer sanitizer or candidate-construction fault
  replaces only unverified text with the fixed `[content unavailable]` fragment,
  retains accepted text, and records a bounded `public_projection_omissions`
  counter without raw content. Later independent text remains deliverable.
  Callback persistence/acknowledgement failures, invalid receipts, and Artifact
  failures retain their existing failure paths.
- **Compatibility / retirement:** no parallel answer protocol is introduced.
  The generic structured-payload path check no longer re-rejects
  `message.delta` paths. Assertions that treated raw terminal text as authority
  over delivered text are retired. The removed
  `claude_agent_sdk_public_projection_failed` category and
  `projection_failure_reason` field remain absent.
- **Regression proof:** gate tests cover stateful redaction, exact replacements,
  local fault recovery, and terminal non-replay; adapter tests cover transactional
  candidate state; runner tests prove continuous event identities and exact
  receipts after an omitted fragment; Worker tests prove successful receipt and
  Artifact delivery remain independent from the omission diagnostic.
- **Evidence ceiling:** source and local tests do not prove packaged or deployed
  behavior. Runtime acceptance starts from the exact packaged image.
- **Stop conditions:** exposing structured private fields, raw executor data,
  storage keys, credentials, or rejected text; weakening exact replacements,
  stateful secret handling, event persistence, receipt reconstruction, required
  Artifact validation, or changing SSE v4, Runs terminal, Artifact, Tool/Skill
  admission, or frontend contracts requires a revised contract.

## Change Contract: hidden model Thinking content

- **Owner and scope:** Execution maps the Run preference to SDK `effort` and
  `thinking.display`; Chat presentation does not expose model thinking.
  `auto/low/medium/high` are the canonical effort values; legacy `off` inputs
  normalize to `auto`. The executor prompt names user-visible progress as
  work-progress commentary, never summarized Thinking. Model selection remains
  unchanged.
- **Behavior:** every level uses adaptive thinking with `display=omitted`, so the
  model may reason internally without returning Thinking text. The runner does
  not publish returned `ThinkingBlock` text. Safe Assistant suffixes now use
  versioned part deltas and explicit answer/work classification; existing
  commentary/worktrace and v1 answer history remain readable. Completed main SDK
  text is the sole public text source, with persisted facts and receipt v2 governing
  final selection. SDK success may have no public answer; no Result body is used to
  fill that gap. Optional files remain independent.
- **Compatibility and retirement:** the current
  [streaming message design](../implementation/streaming-message-parts-design.md)
  replaces classification-time buffering and worktrace production and specifies
  the coordinated schema, receipt and reader release. Historical rows remain
  immutable. The Thinking prompt and effort/display exclusions remain unchanged.
  `claude_sdk_thinking_summary`
  remains an authenticated legacy callback write path, and `thinking.*` readers
  remain for those callbacks and retained persisted history; the current Runner
  does not produce either event family, and the current Chat UI displays neither
  body nor status. Remove the legacy write path and readers together after
  deployed executors have crossed the release and retained old events have
  expired under the owning lifecycle policy.
- **Acceptance:** tests prove every level uses adaptive thinking with omitted
  display, `auto` sends no explicit effort, the prompt requests work-progress
  commentary without summarized-Thinking language, an unexpected Thinking block
  creates no public answer event, and live plus historical frontend parts contain
  no rendered thinking content.
- **Stop conditions:** any need to expose model Thinking text again, alter effort
  semantics, infer Thinking from ordinary answer text, or change SSE/Run terminal
  authority requires a revised contract.

## Raw Stream Failure Diagnostics

Sandbox-brokered SDK runs with an `on_text` callback validate raw `StreamEvent`
framing before public answer projection. On the first `raw_frame_invalid` rejection,
private `projection_failure.frame_shape` records only allowlisted event, block,
and delta type labels, the message/open-block/index state, and the failed
validator guard. `preceding_frames` contains at most four accepted frames in
arrival order, with those same structural labels except the guard; the rejected
frame is never in that list. Unknown types map to `other`; no raw frame, payload,
text, identifier, index value, or tool input is persisted. Sandbox normalization
and Runs admin diagnostics independently enforce the bounded closed shapes. An
older record without `preceding_frames` remains readable. This evidence locates
the first SDK-delivered protocol conflict, but cannot prove whether the model,
bundled CLI, SDK transport, or a replay produced it; compare the affected
release's actual SDK/CLI versions and approved upstream evidence before changing
the framing validator. This adds no ordinary-user field and changes neither the
fail-closed decision nor text publication. No superseded production path,
assertion, or selector is in scope.

## Redis Lifecycle Authority

`REDIS_MAX_CONNECTIONS=10` controls `Redis.from_url(max_connections=...)` for
each process. Queue and authentication operations acquire lightweight handles
to the same event-loop-local pool. Releasing an operation handle does not close
the pool. API and worker shutdown close the current loop's pool; a successful
close permits later reconstruction. Cross-loop reuse and use after release or a
failed close are rejected. Public errors use constant codes and do not include
the Redis URL or credentials.

The Redis server's `maxclients` setting is intentionally unchanged. This change
also does not alter sandbox limits, model-gateway capacity, tenant limits, or
the per-user admission ceiling.

## External Acceptance

- Build the Docker image and repeat the installed-SDK import smoke in a
  Docker-capable environment.
- On the operator-approved Docker host, verify the exact commit/image and exercise the 10-worker profile,
  global 10-run ceiling, Redis pool bound, queue behavior, and ordinary-user
  per-user ceiling of 3.
- Treat those runtime results independently from local source, review, and CI
  evidence.
