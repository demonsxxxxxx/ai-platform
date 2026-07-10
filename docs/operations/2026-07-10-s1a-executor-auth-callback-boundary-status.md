# S1A Executor Authentication And Callback Boundary Status

Status:
- [x] Phase 0: delegated authoritative brief accepted in clean worktree at `124a09c`; missing `docs/operations/2026-07-10-security-release-blocker-triage.md` here is expected because that review artifact is untracked in the scheduling root, not part of `origin/main`.
- [x] Phase 1: initial scope and ownership audit completed. Planned write set is limited to `app/runtime/sandbox/contracts.py`, `app/runtime/sandbox/executor_app.py`, `app/runtime/sandbox/executor_client.py`, `app/runtime/sandbox/container_provider.py`, `app/runtime/sandbox/runtime.py`, `tests/test_sandbox_executor_app.py`, `tests/test_sandbox_executor_client.py`, `tests/test_sandbox_container_provider.py`, and `tests/test_sandbox_runtime.py`. No ownership conflict found with the Capability Distribution lane's protected files.
- [x] Phase 2: current-state security scan completed. Confirmed gaps in current source: executor `/v1/tasks/execute` accepts unauthenticated requests; runtime forwards caller-provided `callback_url`; executor tool-permission callback trusts caller-provided `callback_base_url`; Docker provider publishes `18000/tcp` without a loopback host bind.
- [x] Phase 3: red tests written and observed. Initial failures confirmed missing executor auth injection, unrestricted callback forwarding, unchanged callback token binding, and Docker host-port exposure; early pytest setup also required creating the workspace-local `.pytest-tmp/` parent directory before using nested `--basetemp` children on Windows.
- [x] Phase 4: implementation landed in the allowed sandbox lane only. Runtime now derives trusted callback targets and lease-scoped callback token ids; executor app requires an executor credential, validates trusted callback scope, and rejects replay; Docker/OpenSandbox providers now inject a per-lease executor credential and Docker loopback-only port binding.
- [x] Phase 5: local verification completed on this worktree:
  - `python -m pytest tests/test_sandbox_executor_app.py -q --basetemp .pytest-tmp\\s1a-green-app2`
  - `python -m pytest tests/test_sandbox_runtime.py -q --basetemp .pytest-tmp\\s1a-green-runtime2`
  - `python -m pytest tests/test_sandbox_container_provider.py -q --basetemp .pytest-tmp\\s1a-green-provider2`
  - `python -m pytest tests/test_runtime_callbacks.py tests/test_sandbox_contracts.py -q --basetemp .pytest-tmp\\s1a-verify-callbacks`
  - `python -m compileall -q app tools scripts`
  - `git diff --check`
- [x] Phase 6: independent sub-agent review completed via agent `019f4ca9-607a-78a0-be2f-09f5b25336ae` (`James`) and was closed after intake. Review findings fixed and re-verified:
  - P1 fix: trusted callback allowlist now rejects link-local/reserved IPs such as `169.254.169.254` and enforces a callback port allowlist (`80`, `443`, `8000`, `8020`).
  - P1 fix: executor scope binding now fails closed with `executor_scope_not_configured` when `AI_PLATFORM_SESSION_ID` or `AI_PLATFORM_RUN_ID` is missing, and rejects mismatched session/run even with a valid executor credential.
  - Post-review verification: `python -m pytest tests/test_sandbox_executor_app.py tests/test_sandbox_runtime.py tests/test_sandbox_container_provider.py tests/test_runtime_callbacks.py tests/test_sandbox_contracts.py -q --basetemp .pytest-tmp\\s1a-final-verify` -> `118 passed`.
- [x] Phase 7: GitHub evidence chain recorded.
  - Issue: `#373` (`S1A: executor authentication and callback boundary hardening`)
  - Branch: `codex/373-s1a-executor-auth-callback-boundary`
  - PR: `#374` (`fix: harden sandbox executor auth boundary`)
  - Head: `ac3bcd4b07bc9276f2432d9eaadbc6c396081ab4`
  - PR comment posted with sub-agent review substitute scope, findings, fixes, and local verification evidence.
  - Current CI state on PR `#374`: `backend required` = success, `projection audit, lint, build, trace` = success, `packaged image build` = success, `frontend required` = success.

Constraints:
- Do not modify `app/worker.py`, `app/repositories.py`, `app/schema.sql`, `app/routes/runs.py`, `app/executors/claude_agent_worker.py`, `deploy/**`, `Dockerfile`, `frontend/**`, or Capability Distribution-owned schema/repository/run/worker files.
- Do not copy or depend on dirty-root-only review artifacts.
- Do not claim 211 deployment, B2 closure, or 211 verification from this lane.

Target design baseline:
- Runtime must derive callback targets from trusted platform settings, not from caller-provided `callback_url` or `callback_base_url`.
- Executor dispatch must require a per-lease credential and fail closed on missing, wrong, or replayed use.
- Docker executor exposure must stay on internal-only or loopback-only host bindings.
- Logs, projections, and persisted lease payloads must not leak executor credentials.
