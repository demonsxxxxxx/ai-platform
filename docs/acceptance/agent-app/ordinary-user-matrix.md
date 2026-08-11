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
| Capability | Fixed Skill ID/version plus private Skill/MCP hook evidence bound to the exact run attempt |
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
| Explicit start | Opening the Agent Workspace creates no session. Clicking Start creates exactly one Agent Conversation pinned to the recorded Agent identity, Revision, and hash. Response loss, refresh, and concurrent retries reuse the same caller operation identity and return that one session without a second creation audit; attempting to bind that identity to a different workspace, Agent, Revision, or title fails closed. |
| Safe welcome and starters | The welcome message is display-only. Selecting a starter fills the composer or, after explicit send, produces one ordinary user message. Neither becomes a system prompt or an automatic user message. |
| Dedicated submission | The workspace submits to the Agent App run entry point with message, submission ID, authorized file IDs, and timezone only. Body, query, or header attempts to override Agent, Revision, hash, instructions, model, Skill, MCP, or ACL fail before storage or dispatch. |
| Server injection | The accepted run restores the exact pinned definition and injects the server-owned Expert Instruction and fixed capabilities. Ordinary-user configuration APIs, durable events, errors, and logs do not expose the instruction directly. Because it enters model context, it is not a secrets store and model output may reflect it; administrators must not put credentials or other secrets in it. |
| Conversational file admission | A newly bound Expert whose Skill supports or requires file input accepts an ordinary no-file greeting such as `你好`; binding alone never forces file-task admission. A standalone explicitly selected file-required Skill rejects only when neither current nor authorized reusable files exist, returns `file_required_for_skill`, preserves the draft, and shows an actionable upload prompt instead of a generic send failure. Current uploads and authorized historical reuse both pass. |
| Run authorization | New run, retry, resume, and copy each recheck session/user/tenant ownership, current ACL, publication, model, Skill version, and MCP availability. An unpublish or ACL race fails closed with no upstream dispatch. |
| Capability truth | User-visible state progresses only through safe meanings for loaded capability, verified invocation, and verified completion. `actually_invoked` and bounded attempt/completed/failed counts are backed only by validated SDK hook evidence for the selected Skill, its authorized dependency closure, or authorized MCP identities. Each observed invocation is persisted privately as one requested-to-terminal pair in an idempotent exact-attempt batch. Missing lifecycle hooks fail before model dispatch. Selected, staged, inferred, platform-runner, and executor-native facts never claim invocation. |
| Autonomous Skill | A registered Skill group may remain visibly uninvoked while the Expert Run succeeds. Once validated hook evidence shows an invocation attempt, a later failure must not be projected as “not invoked”; a recovered retry retains `partial_failure`, while completion still requires an exact successful terminal hook. |
| Native tool sandbox | Every governed SDK Run receives the same platform-owned local tool set. The primary Docker workspace root is read-only with only exact runtime, delivery, SDK state, and broker-staging mounts writable; internal metadata and staged Skills remain read-only. Native commands execute in a networkless, attempt-bound sidecar that can read only authorized inputs, context, and staged Skills and can write only delivery output through its bounded workspace view. Skill content cannot widen tool, file, credential, network, MCP, or control-plane authority. |
| Shared SSE | Live events and reconnect/history use the shared public streaming adapter with lossless cursor behavior, no duplicate semantic transitions, and terminal parity after refresh. After capability use, success requires a post-verification public stream suffix matching the terminal answer. No raw source, internal path, command, credential, private payload, or queued Expert instruction reaches the ordinary user or dead-letter record. |
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
