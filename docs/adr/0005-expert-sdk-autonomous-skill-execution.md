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

File admission follows the same distinction. Binding an Expert to a Skill that
supports file input does not make every Expert turn a file task, so a new Expert
conversation can accept an ordinary greeting with no file. A standalone,
explicitly selected Skill may still declare file input as required; when neither
the current request nor authorized conversation continuity supplies a file, the
server returns the stable `file_required_for_skill` rejection before persistence.
The client maps that code to an actionable upload message and retains the draft.

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

Every admitted Agent SDK Run receives a provider-owned sandbox-local tool set;
Skill package content cannot add or remove runtime authority. Docker admits
`Read`, `Glob`, `LS`, `Bash`, `Write`, and `Edit`, with Bash routed through the
networkless native-command broker. Governed OpenSandbox admits `Read`, `Glob`,
`LS`, `Write`, and `Edit`, but not Bash: its executor does not yet provide an
equivalent process-isolated command broker, and its provider profile is an
upper bound that removes any broader worker subject. Workspace path policy,
command timeouts, the sandbox security profile, and Run resource limits
constrain the admitted tools. MCP tools, external network access, credentials,
and control-plane operations remain separately authorized.

Native commands execute through a networkless sidecar. It receives a
controller-owned workspace view: authorized inputs, context, and staged Skills
are read-only; only the delivery directory and authenticated broker socket are
writable. The sidecar does not receive the primary workspace root or incidental
hidden files.

Local side-effect tools are serialized from their accepted `PreToolUse` hook
through the matching `PostToolUse` or `PostToolUseFailure` hook. A Docker Bash
command therefore completes descendant-process cleanup before primary-executor
Write or Edit starts, closing the shared-delivery symlink race window.

The primary Docker executor receives the workspace root read-only. Only
`outputs/delivery`, runtime markers, the SDK home/config/temp directories, and a
context broker staging alias are writable. The public `context/` path remains a
read-only workspace view; the staging alias is outside the SDK workspace and is
used only by the platform context broker. `.pins`, staged `.claude` material, and
the native broker socket view are read-only. Unknown top-level hidden entries
fail container admission before creation.

When a governed Skill or MCP capability is registered, SDK lifecycle hooks are
mandatory. Missing hooks fail admission before model dispatch. A selected Skill
and its authorized dependency closure form one bound Expert capability group.
Only validated hook transitions count invocation attempts, completions, and
failures. A failed attempt remains visible even if a later retry completes; it
is never relabeled as not invoked.

The `invocation_requested` evidence callback must be durably acknowledged before
the corresponding Skill or MCP `PreToolUse` hook returns allow. A missing,
rejected, or failed acknowledgement denies the tool before its side effect and
fails the Run closed.

The worker persists each validated Skill or MCP lifecycle as one private,
idempotent run-event batch bound to the exact queue `attempt_id`. Each record is
an allowlist of capability kind and canonical identity, tool-call ID, lifecycle
phase/status, evidence source/trust basis, and declaration digest. Prompt text,
tool arguments, tool responses, endpoints, credentials, and private errors are
not persisted in this evidence. Release verification requires each observed
invocation to contain exactly one `invocation_requested` followed by exactly one
`completed`, `failed`, or worker-owned `outcome_unknown` record in that same
run/attempt. `outcome_unknown` is emitted only when a failed Run ends after the
durable allow record but before an SDK terminal callback can be proven; it blocks
the platform from treating the external side effect as safely retryable.

Worker exception logs carry fixed event/phase names and a diagnostic ID. Public
errors and dead-letter records use fixed messages or codes; exception text and
queued payloads are not copied into either boundary.

Public answer text is sealed while a governed capability is active. After the
latest verified terminal hook, a successful terminal answer must end with the
exact public text observed after that verification boundary. Missing or
unmatched terminal text fails closed instead of producing an empty or truncated
successful answer.

## Consequences

- A conversational turn may succeed with a Skill registered but uninvoked.
- Current uploads and authorized historical file reuse remain valid for explicit
  file tasks; there is no client-only file preflight that can block server-owned
  continuity rules.
- Adding a governed Skill does not require platform Python branches.
- Recovered capability calls can finish the Run while retaining a truthful
  partial-failure projection and bounded counts.
- Historical platform-controlled Expert execution and raw dead-letter replay are
  not supported.
- Deterministic domain workflows require a separate product contract instead of
  overloading Expert chat.
