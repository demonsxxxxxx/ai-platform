---
status: accepted
---

# Let the Agent SDK autonomously dispatch authorized Skills

Design ID: `ai-platform.agent-sdk-autonomous-skill-dispatch.v1`

## Context

An enterprise administrator builds an Agent by placing governed Skills into
its capability set. The platform must pin and authorize those exact Skill
versions, but it must not predict which Skill the user's next request needs.

The previous Agent Profile contract bound one selected Skill as a required
capability. Admission also interpreted a Skill's accepted file input modes as
proof that every run required an attachment. This collapsed three different
facts: what the Agent may use, what the SDK chooses to invoke for one request,
and what data the current conversation has made available.

## Decision

1. An Agent Profile Revision owns an immutable Agent Skill Set containing one
   or more exact, governed Skill versions and their authorized dependency
   closure. Adding a Skill grants availability; it never means that every run
   must invoke that Skill.
2. Admission reauthorizes and pins the Agent revision, Skill versions, model,
   MCP tools, tool and network policy, tenant scope, and resource limits. The
   execution adapter stages and registers the authorized Skill Set with the
   selected Agent SDK.
3. The Agent SDK decides whether to invoke a registered Skill, which Skill to
   invoke, and in what order from the current request, conversation context,
   and each Skill's governed description and instructions. A run may succeed
   without a Skill invocation.
4. Invocation evidence remains server-owned. Selection, staging, SDK
   registration, model text, file paths, and client claims never count as
   actual invocation or completion.
5. Skill input and output modes describe capability. They may drive safe UI
   hints, upload filters, materialization, and candidate ranking, but they do
   not create a generic per-run attachment requirement. Missing data may block
   only an explicitly modelled action whose immutable contract requires that
   data; it must not disable ordinary conversation or follow-up questions.
6. Authorized files form Attachment Context. A user may upload a file once and
   continue to discuss it within the same authorized conversation. Explicitly
   supplied and reused files still pass tenant, owner, session, content, size,
   and immutable run-snapshot authorization.
7. Agent Profile input-type, file-type, and expected-output fields are not
   independent execution authority. Where retained for public presentation,
   they are optional or derived from pinned Skill manifests. They must not
   duplicate or override governed Skill contracts.
8. Market copy such as description, welcome message, starter prompts,
   recommended tasks, capability summary, expected outputs, category, avatar,
   and data-access notice is optional presentation content. It must not be a
   prerequisite for saving, testing, publishing, or executing an Agent.
9. Model selection and optional MCP/tool grants remain administrator-governed
   upper bounds. Browser requests and the Agent SDK cannot expand them at run
   time.

## Consequences

- Agent Builder's essential workflow becomes identity, Skill Set, access scope,
  and publication; presentation copy and execution overrides remain optional.
- The runtime contract changes from a singular `required_skill` to an
  authorized, version-pinned Skill Set. Legacy singular profiles are read as a
  one-member set without preserving mandatory invocation semantics for new
  runs.
- File-capable Skills can answer text-only follow-ups. When an action genuinely
  needs a file and none is available, the product asks for one instead of
  returning a generic send failure.
- Platform policy continues to provide hard security and reproducibility
  boundaries while Agent behavior remains portable across Claude Agent SDK,
  OpenAI, Pi, or another conforming execution adapter.

## Supersession boundary

This ADR supersedes ADR 0005 and the Agent-first implementation contract only
where they require one exact Agent Profile Skill to be invoked on every run.
Their separate decision that ordinary Harness chat is not a synthetic Skill
remains accepted.

## Rejected alternatives

- Treat the selected Skill as mandatory on every run: this prevents the SDK
  from routing requests and makes unrelated follow-up questions fail.
- Infer attachment requirements from accepted file types: capability metadata
  cannot express whether a particular user action requires a file.
- Let the browser choose arbitrary Skills per message: this bypasses the
  immutable Agent revision, enterprise distribution, and replay authority.
- Remove server-owned Skill authorization and evidence: autonomous dispatch is
  bounded autonomy, not permission for the SDK to expand its capabilities.

## Evidence boundary

This ADR records the accepted product and architecture direction. It does not
claim that the current schema, Builder, admission path, workers, execution
adapters, browser flow, or deployed runtime already conforms. Source changes,
compatibility migration, focused tests, and deployed product acceptance remain
separate closure work.
