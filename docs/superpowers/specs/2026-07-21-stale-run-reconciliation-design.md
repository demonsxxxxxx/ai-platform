# Stale Run Reconciliation Design

## Objective and scope

Recover durable `queued` or `running` rows that no longer have any queue,
worker-execution, or sandbox owner, so they cannot permanently consume
per-user admission. This slice uses the existing worker maintenance loop and
the existing terminalization/parent-rollup path. It changes no schema,
dependency, public route, status vocabulary, or frontend contract.

Production scope is limited to `app/worker_main.py`, `app/repositories.py`,
`app/queue.py`, and the existing admission seam in `app/routes/chat.py`.
Focused tests exercise ownership proof, maintenance recovery, the guarded
repository transition, and the bounded admission retry.

## Invariants and failure modes

1. Time is only a candidate bound, never orphan proof. A candidate must be
   older than the configured bounded staleness window and have no active
   sandbox lease.
2. Redis must positively prove that the exact tenant/run has no queued,
   processing, retry, or metadata record. Inspection is bounded and fails
   closed on Redis errors, malformed ownership data, or an incomplete scan.
3. Redis absence is checked twice. The second check runs while the scoped DB
   transition is pending; if an owner appears, no DB write or queue cleanup is
   performed. A final tenant/workspace/user/run/status/staleness/no-active-
   lease CAS prevents stale observations from overwriting current state.
4. A cancel-requested orphan is staged as `cancelled`. Any other orphan is
   staged through the existing compatible `failed` contract with an explicit
   `stale_run_interrupted` error. Nothing is inferred as succeeded or silently
   cancelled.
5. The existing permission terminalization module performs bounded drain,
   terminal event/audit creation, run-step closure, and child/parent
   reconciliation. Maintenance never ACKs or releases a live queue message.
6. Repeated maintenance is idempotent: only the first guarded stage emits the
   reconciliation audit/event, and terminal or concurrently changed rows lose
   the CAS without cleanup.

Primary failure modes are false orphan detection, a queue-owner race, a DB
terminalization race, and partial permission drain. The two authoritative
Redis checks plus guarded DB CAS address the first three; the durable existing
permission target and maintenance retry address the fourth.

## Maintenance flow

Worker startup and periodic maintenance first perform existing expired lease
cleanup and queue lease reclamation. They then select a bounded oldest-first
set of stale active rows. For each row:

1. inspect exact-run Redis ownership with a bounded complete scan;
2. if absent, open a transaction and inspect Redis again;
3. stage the scoped terminal intent with the DB CAS;
4. outside that transaction, drain the existing permission terminalization;
5. if the run became terminal, invoke the existing child/parent reconciliation.

When admission is already blocked, the existing user advisory lock also owns
one bounded principal-scoped pre-check. It applies the same double Redis check
and DB CAS, advances one permission terminalization batch in the same
transaction, then re-runs admission. It does not run on ordinary accepted
submissions and does not resubmit work.

Fresh progress, any queued item, processing/retry metadata, an active worker
owner, or an active sandbox lease causes a no-op. Queue data is never deleted
by reconciliation; an owner that appears remains authoritative and processes
or discards its own message through the normal worker path.

## Compatibility and verification

Reloads already project `failed`, `cancelled`, `error_code`, and terminal run
events, so no route or UI change is required. Once terminal, the existing
active-run admission count excludes the row; no automatic resubmission occurs.

Verification commands:

```text
python -m pytest tests/test_chat_routes.py tests/test_queue.py tests/test_worker_main.py tests/test_repositories.py -q --basetemp .pytest-tmp
python -m pytest tests/test_tool_permission_lifecycle.py tests/test_run_control_routes.py tests/test_worker.py -q --basetemp .pytest-tmp
python -m compileall -q app tools scripts
git diff --check
```

No remote run creation, manual row mutation, admin cleanup, browser, SSH,
Docker, deployment, or GitHub action is part of this implementation task.
