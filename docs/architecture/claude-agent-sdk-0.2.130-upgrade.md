# Claude Agent SDK 0.2.130 And Runtime Capacity Profile

## Decision

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
| Messages | `AssistantMessage`, `TextBlock`, and `StreamEvent` retain the consumed shapes | Partial assistant text is progress only and cannot prove tool or Skill success |
| Terminal result | `ResultMessage` adds `terminal_reason` while retaining result/error/session/usage fields | Structured `ResultMessage` remains terminal authority; abnormal reasons fail closed |
| Partial streaming | `include_partial_messages=True` remains supported | Public answer deltas continue through the existing safe projection callback |
| Settings | `setting_sources` remains supported | Only explicit project settings are loaded after platform-controlled scrubbing |
| Permissions | `permission_mode`, allowed tools, disallowed tools, and `can_use_tool` remain supported | Platform authorization, admission, sandbox, and context remain authoritative |
| Limits | `max_turns`, `effort`, and `max_thinking_tokens` remain supported | Max-turn termination maps to a stable public platform error |
| Process context | `cwd` and `env` remain supported | The runner supplies the governed workspace and an allowlisted environment |
| Abort/cancel | `query` has no explicit interrupt method; task cancellation closes iterator/subprocess work | Outer cancellation propagates; SDK abort terminal reasons map to cancellation |

The target wheel is also exercised in an isolated local environment without a
model or network call. That smoke imports the installed distribution, checks
metadata and signatures, constructs every option and hook shape used here, and
instantiates the stream and terminal message types.

## Change Contract: public answer projection failures

- **Owner:** the executor public-answer gate and Claude Agent SDK adapter for
  classification; the Runs public-terminal projection for ordinary-user output;
  the generated SSE v4 contract and frontend terminal catalog for transport and
  local presentation.
- **Bounded paths:** the public-answer gate, Claude SDK runner, existing sandbox
  error-code allowlists, Runs public-terminal projection and its existing Chat,
  Run, provenance, and v4 consumers, the v4 schema and generated contracts, the
  existing frontend v4 adapter and terminal catalog, owning tests, and this
  contract.
- **Invariants:** projection remains fail-closed; raw SDK/Hook errors, answer
  bodies, paths, tool inputs, credentials, and executor-private identifiers never
  enter ordinary-user output; true tool admission failures keep their existing
  code.
- **Acceptance:** every gate failure retains its first allowlisted reason and
  returns `claude_agent_sdk_public_projection_failed`; Runs exposes that fixed
  category and the reason only when the terminal status is `failed`, the error
  code matches, and the reason is allowlisted; historical failures without a
  retained reason remain unknown rather than inferred.
- **Regression proof:** gate tests cover size, sanitizer, replacement, terminal
  consistency, and upstream-projector reasons; SDK/sandbox tests prove the new
  code is distinct from tool admission and contains no raw failure text; Runs,
  Chat, v4, route, and frontend live/replay tests prove ordinary-user projection
  and historical hydration preserve only the fixed category and reason, while
  schema/catalog parity rejects drift.
- **Evidence ceiling:** source and local tests cannot recover a reason discarded
  by an older deployed image; runtime acceptance begins with a new failure from
  the exact packaged image.
- **Rollback:** remove the optional diagnostic reason and new public code while
  retaining fail-closed projection; no data migration is required.
- **Stop conditions:** any request to expose raw executor text, weaken
  sanitization, alter tool admission, or add an ordinary-user private-diagnostics
  endpoint requires a revised contract.

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
