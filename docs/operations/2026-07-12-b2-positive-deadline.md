# B2 Positive Executor Deadline

Status: `PR ready`

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
- [x] Phase 6 - Publish
  - Issue [#402](https://github.com/demonsxxxxxx/ai-platform/issues/402) is open.
  - Implementation commit: `3a699bf8f26baa24c6084a3f94f3477393cf1853`.
  - Branch `codex/b2-positive-deadline` was pushed without force.
  - Draft PR [#405](https://github.com/demonsxxxxxx/ai-platform/pull/405) links the issue without auto-close language.
- [x] Phase 7 - Revision 38 review fixes
  - Controller epoch `controller-20260711-1945`, board revision 38, lane generation 1, phase sequence 4.
  - Starting head `22296c9892cb96ea2f5646fafe42c76f9cd7f768`; scope fingerprint `sha256:78b3270bf4ab8beabead15f39f60218d94ebdc0ed578779b65d776ea0acf8a4a`; clean starting worktree fingerprint `sha256:1e147359c9a84868948c52f227d69ea3f7eba8f9f0f78c6a81421779c3011ee3`.
  - Executor RED: 7 failed and 2 passed for invalid deadlines and async callable compatibility; focused GREEN: 9 passed; full executor GREEN: 37 passed.
  - Evidence RED: 4 failed and 1 passed for current-only provenance and finite values; focused GREEN: 5 passed; combined generator/verifier/readiness GREEN: 101 passed.
  - Full affected suite: 156 passed across executor, runtime, generator/verifier, and readiness tests.
  - Imported resource-deadline files no longer fall back as current proof; only the current platform diagnostic result can populate resource deadline evidence.
  - Runtime subject now derives from Docker inspect image ID, requested/observed image equality, matching source labels, and `source_tree_dirty=false`; missing or conflicting identity remains unverified.
  - Bounded admin projection is retained only when an actual same-run failed callback supplies a projection that passes the existing safety contract; no redaction or error fields are fabricated.
  - Bool, malformed, NaN, and Infinity deadline inputs fail closed; non-finite elapsed observations are rejected by generator, verifier, and readiness.
  - Partial, async callable object, and wrapped async runners remain supported; runner-raised `TimeoutError` remains `executor_runner_failed`.
- [x] Phase 8 - Revision 38 publish
  - Fresh compile, diff, eight-path scope, and added-line secret checks passed.
  - Review-fix implementation commit: `a26997bfd6291dcf59f905622551b59b454e7db1`.
  - The existing branch was pushed without force to draft PR #405; no merge, 211, deployment, or B2 closure was performed.

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
