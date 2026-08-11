# ADR 0005: Let the Harness Choose Bound Expert Skills

## Status

Accepted on 2026-08-11.

## Context

An Expert supplies a server-owned instruction and one or more governed Skills, but a
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

The Expert Instruction is integrity-protected configuration, not a secrets store.
Ordinary-user configuration projections, events, errors, logs, and dead letters
must not expose it directly, but the instruction is presented to the model and can
influence or be reflected in model output. Administrators must never put passwords,
tokens, or other secrets in it. Dead-letter records retain only a payload digest
and byte count rather than the queued payload.

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

Native commands execute through a networkless sidecar. It receives a
controller-owned workspace view: authorized inputs, context, and staged Skills
are read-only; only the delivery directory and authenticated broker socket are
writable. The sidecar does not receive the primary workspace root or incidental
hidden files.

When a governed Skill or MCP capability is registered, SDK lifecycle hooks are
mandatory. Missing hooks fail admission before model dispatch. A selected Skill
and its authorized dependency closure form one bound Expert capability group.
Only validated hook transitions count invocation attempts, completions, and
failures. A failed attempt remains visible even if a later retry completes; it
is never relabeled as not invoked.

Public answer text is sealed while a governed capability is active. After the
latest verified terminal hook, a successful terminal answer must end with the
exact public text observed after that verification boundary. Missing or
unmatched terminal text fails closed instead of producing an empty or truncated
successful answer.

## Consequences

- A conversational turn may succeed with a Skill registered but uninvoked.
- Adding a governed Skill does not require platform Python branches.
- Recovered capability calls can finish the Run while retaining a truthful
  partial-failure projection and bounded counts.
- Historical platform-controlled Expert execution and raw dead-letter replay are
  not supported.
- Deterministic domain workflows require a separate product contract instead of
  overloading Expert chat.
