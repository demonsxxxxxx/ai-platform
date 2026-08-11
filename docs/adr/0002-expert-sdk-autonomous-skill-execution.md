# ADR 0002: Let the Harness Choose Bound Expert Skills

## Status

Accepted on 2026-08-11.

## Context

An Expert supplies a private instruction and one or more governed Skills, but a
conversation can contain domain work, clarification, and ordinary questions.
Treating a bound Skill as a mandatory workflow made the platform route by
specific Skill IDs, reuse files implicitly, and force artifact-producing work
for conversational turns.

## Decision

Every Expert Run restores the pinned Expert Instruction into the Harness system
channel and registers the exact authorized Skill set. The Harness decides whether
to invoke a Skill from the current user message and conversation context. The
platform does not classify translation, review, writing, or other domain
workflows and does not require a bound Skill or artifact to be used for a Run to
succeed.

The platform remains authoritative for identity, authorization, immutable Skill
material, tool and file access, sandbox isolation, persistence, invocation
evidence, artifacts, diagnostics, and public error projection. Skill-specific
instructions, scripts, input expectations, and output behavior belong to the
Skill package rather than platform runtime branches.

Every admitted Agent SDK Run receives the same platform-owned sandbox-local
tool set: `Read`, `Glob`, `LS`, `Bash`, `Write`, and `Edit`. Skill package
content cannot add or remove runtime authority. Workspace path policy, the
native-command proxy, command timeouts, the sandbox security profile, and Run
resource limits constrain those tools. MCP tools, external network access,
credentials, and control-plane operations remain separately authorized.

## Consequences

- A conversational turn may succeed with a Skill registered but uninvoked.
- Adding a governed Skill does not require platform Python branches.
- Historical platform-controlled Expert execution is not supported; retired
  definitions may remain readable but cannot create new Runs.
- Deterministic domain workflows require a separate product contract instead of
  overloading Expert chat.
