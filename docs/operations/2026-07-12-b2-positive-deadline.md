# B2 Positive Executor Deadline

Status: `local partial`

- [x] Phase 1 - Freshness and RED
  - Controller epoch `controller-20260711-1945`, revision 35, lane generation 1.
  - Base and starting head: `6a30f23187609ed0c923d64c680994ce4d0df8b7`.
  - Scope fingerprint: `sha256:78b3270bf4ab8beabead15f39f60218d94ebdc0ed578779b65d776ea0acf8a4a`.
  - Accepted phase-2 dirty fingerprint: `sha256:c0155b1ab72253c4750022525b3d51038113127c1d07c3839663b393184ab0d3`.
  - Executor RED: 3 failed and 23 passed; generator RED: 2 failed and 66 deselected; readiness RED: 1 failed and 29 deselected.
- [x] Phase 2 - Executor deadline
  - Positive async deadlines use an explicit asyncio task deadline, preserve caller cancellation, distinguish runner `TimeoutError`, and wait for cooperative runner cancellation cleanup.
  - Timed-out runners emit one terminal `failed` callback and response with `executor_deadline_exceeded`, requested seconds, and observed elapsed milliseconds.
  - Non-positive limits retain the bounded `executor_health_timeout` probe behavior.
  - Sync runners fail closed before invocation when a positive deadline is configured; no thread-based execution can continue after timeout.
- [x] Phase 3 - Truthful evidence
  - The platform probe requests a positive fractional deadline and derives enforcement only from the observed executor response.
  - Accepted evidence binds the run, executor-response source, platform runtime mode, runtime subject, requested deadline, bounded elapsed time, cleanup result, and bounded admin projection.
  - Generic health failures, source-only assertions, missing observations, run/request mismatches, runtime-subject mismatches, and unbounded elapsed values fail closed.
  - CPU, memory, PID, disk, workspace TTL, provider, lease, escape, confidentiality, and OpenSandbox enforcement are not claimed by this slice.
- [x] Phase 4 - Focused verification
  - `tests/test_sandbox_executor_app.py`: 28 passed.
  - `tests/test_sandbox_runtime.py`: 18 passed.
  - `tests/test_sandbox_runtime_211_script.py`: 68 passed.
  - `tests/test_b2_sandbox_readiness.py`: 30 passed.
- [x] Phase 5 - Final local gates
  - Fresh compile, diff, changed-path scope, and added-line secret scans passed before publish.
  - Self-review replaced `asyncio.wait_for` with an explicit task deadline so runner-raised `TimeoutError` remains `executor_runner_failed` while actual pending work is cancelled and collected.
  - Readiness binds the observed timeout runtime subject to the reviewed outer image subject.
- [ ] Phase 6 - Publish
  - Issue [#402](https://github.com/demonsxxxxxx/ai-platform/issues/402) is open.
  - Pending ordinary commit, push, and draft PR without auto-close language.

## Evidence Boundaries

- Current source evidence: local branch and focused tests only.
- Runtime subject: none. No Docker, 211, deployment, runtime smoke, merge, independent review, or B2 gate closure was performed.
- Historical evidence: the revision-33 Critical finding and controller-reported 114-test baseline are inputs, not current runtime proof.
- Stale evidence: the former unconditional `max_seconds_enforced=true` source-regression shape and `max_seconds=0` health fallback cannot prove positive deadline enforcement.

## Self-Review Focus

- Cancellation must propagate from callers and must not be rewritten as timeout.
- Timeout handling must not schedule thread work or leave a runner task active.
- Terminal callbacks and responses must remain bounded and exclude host paths, callback tokens, and raw runtime payloads.
- Evidence must reject late, generic, mismatched, source-only, and runtime-subject-unbound claims.
