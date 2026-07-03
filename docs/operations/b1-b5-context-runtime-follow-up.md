# B1/B5 Context Runtime Follow-Up

This document records the next local runtime-readiness step after PR #307. PR
#307 is `merged` only. It is not `211 verified`, does not close #164/#156/#81,
and is not `gate closable`.

Generate the local verifier result from the repository root:

```powershell
python tools/verify_b1_b5_context_runtime.py --format json
python tools/verify_b1_b5_context_runtime.py --format markdown
```

The verifier target is `local_b1_b5_context_runtime`. It exercises local source
contracts only: bounded `ContextManifest` prompt construction, scoped retrieval
tool wiring, byte-capped file staging, public projection redaction, and the
SessionContinuity persistence design record. It does not call 211, Docker, or a
real Claude SDK service.

## Runtime Readiness Boundary

- Ordinary chat and document workflows must include only bounded
  `ContextManifest` references in executor prompts.
- Large files must be represented as refs with `requires_retrieval=true`; full
  file content is fetched only through scoped retrieval tools.
- `stage_context_file_to_workspace` must enforce a byte cap before creating a
  target directory or writing bytes.
- Public verifier/readiness JSON must not contain storage keys, private payloads,
  object-store locators, sandbox workdirs, or private absolute paths.
- Status remains `local partial` until local checks pass. A branch with passing
  local checks may be called `PR ready`; only fresh post-merge 211 evidence can
  later support `211 verified`.

## SessionContinuity Source Of Truth Design

Current source state uses `InMemorySessionContinuityStore`, which is valid for
source-foundation tests and single-worker development only. Runtime source of
truth should move to DB/Redis instead of relying on worker process memory.

The durable DB record owns the SDK session resume key. The key scope is
`tenant_id`, `workspace_id`, `user_id`, `session_id`, `agent_id`, `skill_id`,
and `model_key`; the stored value is the SDK session id returned or authorized
for that scope. A unique DB constraint on that scope prevents duplicate resume
records after API or worker restart.

Fork isolation is explicit. A forked run stores a separate child resume record
with `parent_resume_key_id`, `fork_reason`, and a generated fork sequence or
nonce. Forks never reuse the parent lock key, and the parent session continues
to resolve to its original SDK session id.

Multi-worker lock ownership should be external to Python process memory. Redis
is the preferred fast lock backend when available, using a lock key derived from
the DB resume record id plus fork id. If Redis is unavailable, the DB fallback is
an advisory lock or lease row with expiry, owner worker id, acquired_at, and
heartbeat fields. The lock serializes SDK resume writes for the same continuity
record, not unrelated runs.

Restart recovery is DB-first. After API or worker restart, resolving the same
scope reads the DB resume record and reacquires the Redis/DB lock before calling
the SDK. Expired locks can be reclaimed only when the owner heartbeat is stale;
reclaimed locks must append audit evidence so operators can distinguish normal
resume from recovery.

DB/Redis split:

- DB owns resume records, fork lineage, recovery audit, and final SDK session id
  reconciliation.
- Redis owns short-lived lock acquisition and heartbeat acceleration.
- DB fallback must preserve correctness if Redis is down, even if throughput is
  lower.

## Retrieval Safety Boundary

`stage_context_file_to_workspace` returns a workspace-relative path only. It
does not expose storage keys or raw private payload. Oversized files fail before
workspace writes, producing a scoped retrieval failure instead of dragging large
or abnormal objects into the worker workspace.

## Non-Closure Labels

- PR #307: `merged`
- This branch before PR: `local partial`
- This branch after passing local verifier/tests: `PR ready`
- Not claimed here: `reviewed`, `merged`, `211 verified`, `gate closable`
