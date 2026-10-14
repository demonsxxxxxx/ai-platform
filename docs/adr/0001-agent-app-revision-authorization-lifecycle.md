# ADR 0001: Pin Agent Conversations and Reauthorize Every Run

## Status

Accepted on 2026-08-05 for the Agent App governance contract tracked by #754;
partially superseded by [ADR 0015](0015-agent-autonomy-and-effect-boundaries.md).

## Current supersession boundary

The original decision below records the former conversation-wide execution pin.
ADR 0015 replaces that pin for ordinary new turns: the conversation retains its
Agent identity and initial revision/hash as provenance, while each new Run
resolves the current published Profile and currently authorized Skill versions.
The accepted Run owns the immutable execution snapshot. Worker dispatch and
retry/resume/copy use that Run snapshot and recheck current authorization and
publication availability; historical rows and hashes are not rewritten.
Builder test conversations separately reject a changed tested revision.

[ADR 0008](0008-agent-sdk-autonomous-skill-dispatch.md) owns autonomous use of the
authorized Skill Set. Model and thinking selections are Run-owned under ADR 0015;
clients cannot override private instructions, Skill references or MCP authority.
Withdrawal, current ACL checks, private-data boundaries, and authorized historical
reads from this ADR remain in force.

## Context

An enterprise expert combines user-facing profile facts with private
instructions, model, Skill version, MCP tools, publication scope, and ACLs. A
mutable conversation binding would let a later publication silently change an
existing conversation. Authorizing only when a conversation starts would also
let retry, resume, copy, or a later run bypass an unpublish or ACL change.

At the same time, withdrawing an expert must not destroy the user's historical
messages or already-authorized durable artifacts.

## Decision

Original decision; the current supersession boundary above applies.

1. An Agent Conversation is created only after an explicit user action and is
   pinned to `agent_id`, immutable Revision, and `content_hash`.
2. A new publication never migrates an existing Agent Conversation.
3. Every new run, retry, resume, and copy restores the pinned definition from
   server authority and reauthorizes principal ownership, tenant, publication,
   ACL, model, Skill version, and MCP capability before dispatch.
4. Withdrawing an Agent App denies new conversations and every new execution
   attempt. Authorized historical messages and durable artifacts remain
   read-only under current read authorization.
5. Clients receive only the safe Agent identity and capability semantics. They
   cannot supply or override private instructions, model, Skill, tools, ACL
   details, or the definition hash.

## Consequences

Consequences of the original conversation-wide pin:

- Conversation behavior is reproducible from an exact immutable definition.
- ACL and lifecycle revocation take effect at every execution boundary without
  rewriting history.
- Historical reads and new execution have intentionally different admission
  rules.
- Retry, resume, and copy cannot be implemented as trusted client shortcuts;
  each must pass the same server-owned authorization gate.
- Additive profile fields require compatibility defaults during rolling source
  deployment. Rollback may stop using new fields but must not delete immutable
  revisions, pinned conversations, or durable artifact records.

## Rejected Alternatives

Original alternatives, before the later Run-scoped decision:

- Pin only `agent_id`: a later publication would mutate existing conversations.
- Authorize once at conversation creation: revoked access could still execute.
- Delete history on withdrawal: this would break auditability and authorized
  access to prior results.
- Let the workspace submit model or capability selectors: this would split
  authority between browser and server.
