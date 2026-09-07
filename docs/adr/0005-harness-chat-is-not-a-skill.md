---
status: accepted
---

# Model base Harness chat without a synthetic Skill

Design ID: `ai-platform.skillless-harness-chat.v1`

## Context

The platform previously represented ordinary conversation as the
`general-agent` agent plus a synthetic `general-chat` Skill. That collapsed two
different authorities:

- the Harness, which owns the private model/tool loop behind a platform adapter;
- a Skill, which is an explicitly selected professional capability with an
  authorized version, release decision, manifest set, staged files, and exact
  invocation evidence.

The synthetic Skill forced ordinary chat through Skill catalog, distribution,
release, snapshot, staging, and SDK registration code even when no professional
capability was selected. It also made the Workbench imply that users needed a
Skill merely to converse with the Harness.

## Decision

New run payloads have an explicit execution identity:

| execution kind | Skill identity | payload schema | executor |
| --- | --- | --- | --- |
| `harness_chat` | `skill_id = null` | `ai-platform.run-payload.v2` | `claude-agent-worker` |
| `skill` | non-empty `skill_id` plus exact version/release/manifests | v1 or a supported successor | the authorized Skill executor |

For `harness_chat`, admission must resolve the active `general-agent` with
`agent_type=chat` and
must not call Skill authorization. Persistence stores `skill_id = null` and
enforces the execution-kind/Skill pair with a database constraint. Queue and
worker identity locks include `execution_kind` so a caller cannot reinterpret a
Harness run as a Skill run or vice versa.

The worker must not resolve a Skill catalog, create Skill snapshots, stage Skill
packages, register the SDK `Skill` tool, require Skill invocation evidence, or
emit `skill_selected`/`skill_staging` facts for `harness_chat`. It may still use
separately authorized MCP tools, context, files, model selection, and sandbox
policy. Attachments remain scoped data inputs; Harness chat receives authorized
metadata plus bounded on-demand read/stage tools without turning data files into
Skills or forcing typed file materialization. A specialized file Skill may
still require eager bounded materialization under the run workspace `inputs/`
directory.

Agent Profiles configure one or more authorized Skills by name. Publication and
admission resolve the current authorized versions; each accepted Run still owns
an exact immutable Skill binding.

## Legacy compatibility

Persisted v1 runs whose Skill identity is `general-chat` remain readable and
replayable through compatibility branches. An upgrade does not delete legacy
database Skill/version rows that historical foreign keys may still reference,
but clean installs no longer seed them, the Workbench never publishes them, and
`general-agent.default_skill_id` is null. New requests that
explicitly select `general-chat` fail with `general_chat_is_not_a_skill`.

Historical source rows are never rewritten: already-queued v1 work can finish
through the dual-read compatibility path, and the original row remains the
audit fact. Copy, retry, and resume upgrade an unprofiled legacy
`general-agent` / `general-chat` source into a new v2 `harness_chat` child with
`skill_id = null`; that child reauthorizes current MCP access without
manufacturing a Skill identity. All other Skill copies, including profile-bound
history, retain pinned Skill authority and continue to fail closed on version,
release, manifest, profile, model, or MCP conflicts.

## Consequences

- Ordinary chat has a smaller and more truthful admission/execution path.
- Professional Skills retain exact authorization, release, staging, and
  invocation evidence instead of becoming optional hints.
- Public capability projections can show general chat without publishing a
  `general-chat` Skill.
- Rolling deployment requires schema support before v2 admission; old v1 rows
  remain valid during the compatibility window.

## Rollout and rollback

Roll forward in this order: apply schema version `2026.08.12.1`; deploy every
worker that can consume both v1 Skill payloads and v2 Harness payloads; then
enable API producers of v2 runs. Do not let an old worker consume a v2 queue.
No bulk rewrite of historical runs or Skill snapshots is permitted.

For rollback, stop v2 admission first and drain or finish already-admitted v2
runs with a dual-read worker. The additive column and constraint may remain;
old producers continue to write non-null Skill rows through the default
`execution_kind=skill`. Dropping the column, constraint, or legacy Skill rows
is not a safe application rollback.

A pre-v2 binary is not directly compatible with the upgraded state: it cannot
parse queued v2 payloads with `skill_id = null`, and its inner Skill join hides
`general-agent` after `default_skill_id` becomes null. Before deploying such a
binary, operators must stop admission, drain every v2 run with a dual-read
worker, and temporarily restore the legacy default binding while its retained
compatibility row still exists. If those preconditions cannot be proved, roll
forward instead of rolling the binary back.

## Evidence boundary

Contract and focused route/worker tests prove source behavior. They do not prove
a deployed database migration, worker rollout, real object storage, sandbox
provider, or browser flow. Those remain exact-image runtime acceptance checks.
