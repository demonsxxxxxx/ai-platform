# Agent App Ordinary-User Acceptance Boundary

## Purpose

This matrix defines the source evidence delivered by the Agent App governance
change and the runtime/browser evidence required after merge. It does not deploy,
create runtime fixtures, mutate a deployment host, or claim ordinary-user acceptance. Local
tests, mocks, CI, and packet shape are source evidence only.

Current execution semantics follow [ADR 0015](../../adr/0015-agent-autonomy-and-effect-boundaries.md)
and [ADR 0008](../../adr/0008-agent-sdk-autonomous-skill-dispatch.md): a new Run
uses the current publication and resolved Skill Set; replay retains the accepted
Run snapshot, and Skill availability does not require invocation.

The post-merge run is owned jointly by the project controller and the single
authorized release owner under the lease and exact-main procedure in
`docs/operations/release-operations-runbook.md`. No step in this document
grants release or runtime mutation authority.

## Required Subject Binding

One acceptance packet must bind every observation to the same subject:

| Field | Required value |
| --- | --- |
| Source | Exact 40-character merged `main` commit |
| Image | Deployed immutable image digest and revision labels matching Source |
| Principal | Redacted ordinary-user identity, tenant, departments, and roles |
| Agent | `agent_id`, the Run-admitted immutable Revision and `content_hash` |
| Conversation | Exact ordinary-user `session_id` and its separate initial revision/hash provenance |
| Execution | Exact `submission_id`, `run_id`, and trace correlation |
| Capability | Fixed Skill ID/version and server-reviewed hook evidence |
| Artifact | Durable artifact ID and authenticated download result |

The reviewed packet belongs under
`docs/release-evidence/agent-app/<commit_sha>/<evidence_id>.json` and must follow
the redaction and provenance contract in `docs/release-evidence/README.md`.
Credentials, private instructions, raw hook payloads, executor paths, commands,
environment values, and storage keys must never appear in committed evidence.

## Acceptance Matrix

| Case | Runtime/browser owner must observe |
| --- | --- |
| Market ACL | An authorized ordinary user sees the enterprise expert and its safe purpose, recommended tasks, examples, inputs, outputs, limits, permission notice, and publication facts; a denied principal cannot discover or open it. |
| Explicit start | Opening the Agent Workspace creates no session. Clicking Start creates exactly one Agent Conversation bound to the Agent identity and initial Revision/hash provenance. Later publication does not rewrite that record. Response loss, refresh, and concurrent retries reuse the same caller operation identity and return that one session without a second creation audit; attempting to bind that identity to a different workspace, Agent, Revision, or title fails closed. |
| Safe welcome and starters | The welcome message is display-only. Selecting a starter fills the composer or, after explicit send, produces one ordinary user message. Neither becomes a system prompt or an automatic user message. |
| Dedicated submission | The workspace submits message, submission ID, authorized file IDs, timezone, thinking effort and optional model selection through the Agent App run entry point. Model choices pass normal server admission. Unknown body fields, query overrides and capability-override headers cannot replace Agent identity, Revision/hash, private instructions, Skill, MCP or ACL authority. |
| Server injection | A new ordinary turn resolves the current published Profile and authorized Skill/dependency versions, then freezes them in the accepted Run. Server-owned private instructions and capabilities never come from the browser. Ordinary-user events, errors, logs and UI do not expose those private facts. |
| Run authorization | Every execution rechecks session/user/tenant ownership, current ACL, publication and capability authorization. Retry/resume/copy preserve the accepted Run snapshot rather than resolving a newer execution definition. An unpublish or ACL race fails closed with no upstream dispatch. |
| Capability truth | User-visible state progresses only through safe meanings for loaded capability and verified completion. `actually_invoked` is backed only by `used_skills_source == executor_hook` for the staged Skill. Selected, staged, inferred, platform-runner, and executor-native facts never claim invocation. |
| Autonomous Skill use | The SDK may invoke any subset of the authorized Skill Set, including none. Non-invocation alone does not fail the Run; unused capabilities remain visibly uninvoked. Every observed invocation still requires exact hook-backed evidence. |
| Shared SSE | Live events and reconnect/history use the shared public streaming adapter with lossless cursor behavior, no duplicate semantic transitions, and terminal parity after refresh. No raw source, internal path, command, credential, or private payload reaches the ordinary user. |
| Durable artifact | `artifact_ready` appears only after a durable artifact record exists. The recorded ordinary user can download through the authenticated contract; unauthorized access fails. Model text or a filename alone never creates readiness. |
| Withdrawal history | After withdrawal, new conversations and every new execution attempt are denied. The same authorized user can still read prior messages and download previously durable artifacts under current read authorization. |

## Source Gate Before Handoff

The source PR must provide focused schema, repository, authority, route, worker,
streaming, redaction, idempotency, concurrency, and frontend behavior tests; a
frontend typecheck/lint/build; Python compile and Ruff checks; exact-ref
repository readiness; and an independent fixed-SHA review. These prove only the
candidate source contract.

## Runtime Decision

The acceptance owner records `passed` only when every row is observed against
the bound deployed subject and cleanup succeeds. A missing binding, unavailable
hook proof, incomplete artifact authority, failed cleanup, or uncertain side
effect is `EVIDENCE_BLOCKED` or `UNKNOWN`, never inferred success. Only the fresh
authorized procedure may call that exact deployed subject `runtime verified`.
