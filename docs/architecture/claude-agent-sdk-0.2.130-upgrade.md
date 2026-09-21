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
| Messages | `AssistantMessage`, `TextBlock`, `ThinkingBlock`, and `StreamEvent` remain adapter inputs; a typed Assistant fragment need not close a whole turn | Raw text deltas stream into the public Assistant body; typed text reconciles missing suffixes; Thinking and non-text deltas are excluded |
| Terminal result | `ResultMessage` adds `terminal_reason` while retaining result/error/session/usage fields | Ordinary `result` text is executor completion input; committed public text and its receipt own streamed content; Runs owns business outcome; files are selected separately |
| Partial streaming | `include_partial_messages=True` remains supported | Raw text feeds the public answer gate immediately; later tool use does not reclassify or withdraw accepted Assistant text |
| Settings | `setting_sources` remains supported | Only explicit project settings are loaded after platform-controlled scrubbing |
| Permissions | `permission_mode`, allowed tools, disallowed tools, and `can_use_tool` remain supported | Platform authorization, admission, sandbox, and context remain authoritative |
| Limits | `max_turns`, `effort`, and `max_thinking_tokens` remain supported | Max-turn termination maps to a stable public platform error |
| Automatic compaction | Bundled CLI `2.1.222` owns ongoing auto-compaction and accepts the platform's `--autocompact` window | The runner sets the window to 80% of the immutable Run input ceiling, caps it at 1M, and does not issue `/compact` when opening or resuming a session |
| Process context | `cwd` and `env` remain supported | The runner supplies the governed workspace and an allowlisted environment |
| Abort/cancel | `query` has no explicit interrupt method; task cancellation closes iterator/subprocess work | Outer cancellation propagates; SDK abort terminal reasons map to cancellation |

The target wheel is also exercised in an isolated local environment without a
model or network call. That smoke imports the installed distribution, checks
metadata and signatures, constructs every option and hook shape used here, and
instantiates the stream and terminal message types.

## SDK-native automatic compaction

Execution computes an automatic-compaction target as 80% of the immutable Run
`max_input_tokens` and passes the result through
`ClaudeAgentOptions.extra_args["autocompact"]`. The target is capped at 1M, with
no platform-imposed minimum. Claude Code still owns its output reserve and safety
buffer, so its actual compaction point may be earlier than the platform target.
`CLAUDE_CODE_MAX_OUTPUT_TOKENS` continues to carry the independent output ceiling.
The inherited `CLAUDE_CODE_MAX_CONTEXT_TOKENS` remains scrubbed because the
platform's governed context-input window is the Run's `max_input_tokens`; there is
no separate raw total-context value.

This replaces the runner-authored resume preflight that inspected context usage
and issued `/compact` before the business query. That preflight, its private
permission-mode branch, `context_native_compact_failed`, and the bootstrap-only
413 response are retired together. The count-tokens gate remains enforced for
every request; all conversation modes now return the same bounded Anthropic
`invalid_request_error` with a `prompt is too long` message so the pinned CLI
can run its native reactive path. HTTP request-body size limits and their 413
response are unchanged.

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
  not publish returned `ThinkingBlock` text. Ordinary Assistant text feeds the
  public `message.delta` projection regardless of later tool use. Explicit
  platform-authored public summaries may still use `commentary.delta`. Ordinary chat consumes `ResultMessage.result`, while
  persisted streamed content is governed by the acknowledged-text/receipt
  contract above. Optional `attach_file` selections are independent. Neither
  ordinary text nor commentary requires structured output. Both frontend
  rendering paths exclude legacy thinking parts.
- **Compatibility and retirement:** no new wire or schema field is added. The
  misleading `public summarized-thinking text` prompt instruction is retired;
  it has no persisted or client compatibility surface. `claude_sdk_thinking_summary`
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
