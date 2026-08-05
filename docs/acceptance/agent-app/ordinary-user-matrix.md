# Agent App Ordinary-User Acceptance Boundary

## Purpose

This matrix defines the source evidence delivered by the Agent App governance
change and the runtime/browser evidence required after merge. It does not deploy,
create runtime fixtures, mutate 211, or claim ordinary-user acceptance. Local
tests, mocks, CI, and packet shape are source evidence only.

The post-merge run is owned jointly by the project controller and the single
authorized release owner under the lease and exact-main procedure in
`docs/operations/211-release-operations-runbook.md`. No step in this document
grants release or runtime mutation authority.

## Required Subject Binding

One acceptance packet must bind every observation to the same subject:

| Field | Required value |
| --- | --- |
| Source | Exact 40-character merged `main` commit |
| Image | Deployed immutable image digest and revision labels matching Source |
| Principal | Redacted ordinary-user identity, tenant, departments, and roles |
| Agent | `agent_id`, immutable Revision, and `content_hash` |
| Conversation | Exact ordinary-user `session_id` |
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
| Explicit start | Opening the Agent Workspace creates no session. Clicking Start creates exactly one Agent Conversation pinned to the recorded Agent identity, Revision, and hash. Repeating the same idempotent start does not create a second conversation. |
| Safe welcome and starters | The welcome message is display-only. Selecting a starter fills the composer or, after explicit send, produces one ordinary user message. Neither becomes a system prompt or an automatic user message. |
| Dedicated submission | The workspace submits to the Agent App run entry point with message, submission ID, authorized file IDs, and timezone only. Body, query, or header attempts to override Agent, Revision, hash, instructions, model, Skill, MCP, or ACL fail before storage or dispatch. |
| Server injection | The accepted run restores the exact pinned definition and injects private instructions and fixed capabilities server-side. Ordinary-user events, errors, logs, and UI do not expose those private facts. |
| Run authorization | New run, retry, resume, and copy each recheck session/user/tenant ownership, current ACL, publication, model, Skill version, and MCP availability. An unpublish or ACL race fails closed with no upstream dispatch. |
| Capability truth | User-visible state progresses only through safe meanings for loaded capability and verified completion. `actually_invoked` is backed only by `used_skills_source == executor_hook` for the staged Skill. Selected, staged, inferred, platform-runner, and executor-native facts never claim invocation. |
| Required and optional Skill | A required Skill without exact hook-backed invocation cannot yield a successful Agent Run. An optional Skill that is not invoked remains visibly uninvoked without converting inference into execution. |
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
authorized procedure may call that exact deployed subject `211 verified`.
