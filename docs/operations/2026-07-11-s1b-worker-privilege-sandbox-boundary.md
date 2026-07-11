# S1B-A Worker Sandbox Execution Boundary

Status: `local partial`

Authoritative source base: `818cc80135a6c4d48c600bc6e9f0ef39cf47ecb1` (`origin/main`, PR #390 merge)

Tracking issue: [#392](https://github.com/demonsxxxxxx/ai-platform/issues/392)

This document covers only the S1B-A source slice. It does not claim complete S1B worker privilege reduction, B2 closure, 211 verification, runtime acceptance, or a closable release gate.

## Phase Status

- [x] Phase 1 - Current-main route and trust-boundary readback completed from the isolated worktree.
- [x] Phase 2 - TDD RED reproduced worker-local writing routes, broker bypass, fake-provider acceptance, and placeholder Admin projection.
- [x] Phase 3 - S1B-A execution-boundary implementation completed locally.
- [x] Phase 4 - Focused affected tests completed locally: `317 passed, 3 skipped`.
- [ ] Phase 5 - Fixed-SHA independent review, PR review evidence, and required CI pending.
- [~] S1B-B - Worker/runtime non-root execution deferred; blocker recorded below.
- [~] 211 deployment and runtime verification explicitly out of scope for this source slice.

## Source Contract

`ExecutionBoundaryDecision` is the single interpreter for Claude worker execution authority. For single-run `claude-agent-worker` work, the admitted tiers `sdk_only_writing`, `document_worker`, and `heavy_sandbox` all require a real SandboxRuntime provider. This includes general chat and nested selected Skills. Unknown tiers and direct multi-agent adapter execution fail closed. `input_modes` is not consulted for execution authority. Non-Claude adapters retain their existing adapter-managed route.

The adapter preflights the configured provider before workspace preparation. `fake`, missing, or otherwise unaccepted providers fail before SandboxRuntime construction, Skill staging, local SDK execution, or controlled subprocess execution. After SandboxRuntime returns, the adapter checks the provider reported by the runtime result itself; settings are not accepted as post-execution proof. Only `docker` and `opensandbox` are accepted providers.

The worker no longer creates `sdk_only_lifecycle_placeholder` leases for any admitted Claude writing tier. SandboxRuntime remains the owner of real lease creation and cleanup. Cancelled, failed, and completed ephemeral execution continues to use SandboxRuntime terminal-specific cleanup reasons; the adapter preserves a runtime `cancelled` terminal result as cancellation rather than success.

## Tool Permission Boundary

The sandbox executor invokes the Claude SDK runner with `execution_policy=sandbox_brokered`. This policy overrides a global `bypassPermissions` setting. Only the fixed local read-only allowlist `Read`, `Glob`, and `LS` bypasses the platform permission callback.

A single catch-all `PreToolUse` hook uses `HookMatcher(matcher=None)` so every other tool, including Bash, Write/Edit variants, Agent, network tools, dynamic `mcp__server__tool` names, and future names, reaches the lease-bound platform broker exactly once. Bash command canonicalization remains in that hook. Missing callbacks, callback exceptions/timeouts, malformed responses, and explicit denial all fail closed. The executor continues to derive its callback token from the lease-bound task request and uses the validated fixed callback origin.

## Lease Evidence

Real runtime proof requires all of:

- provider in `docker|opensandbox`;
- lease payload `source=sandbox_runtime`;
- lease payload `evidence_class=runtime_lease_projection`.

Worker lifecycle placeholders use provider `fake` and `source/evidence_class=sdk_only_lifecycle_placeholder`. Repository rows and audit history are not deleted. Admin Runtime real-lease lists and counts filter placeholders and incomplete evidence, while the repository can still return their historical rows. Test doubles that report a provider exercise routing only and do not create acceptable runtime lease evidence.

## Local Evidence

Initial RED evidence:

- writing tiers returned `False` from the old sandbox predicate;
- fake runtime results mapped to success;
- sandbox executor omitted an explicit brokered SDK policy;
- Admin Runtime included placeholder leases;
- fake provider preflight entered preparation;
- a missing runtime provider fell back to configured `docker`;
- static tool matchers did not cover dynamic MCP tools.

Focused GREEN command:

```text
python -m pytest tests/test_execution_boundary.py tests/test_worker.py tests/test_claude_agent_worker_adapter.py tests/test_sandbox_runtime.py tests/test_sandbox_runtime_cleanup.py tests/test_sandbox_executor_app.py tests/test_runtime_callbacks.py tests/test_admin_runtime_routes.py -q --basetemp .pytest-tmp\s1b-affected-20260711-003
```

Observed result: `317 passed, 3 skipped in 30.52s`.

Additional local gates:

- `python -m compileall -q app tools scripts` exited 0.
- Runtime launch/script integration: `90 passed in 6.13s`.
- Combined changed-scope plus launch/script pre-commit gate: `407 passed, 3 skipped in 33.86s`.
- Post-review changed-scope, context, and launch/script gate: `427 passed, 3 skipped in 41.53s`.
- `git diff --check`, changed-scope validation, and secret-pattern scan exited 0.
- Local Docker is unavailable on this workstation; no Docker command was retried and no container/runtime acceptance is claimed.

The retained private local SDK and controlled-runner helpers are tested directly as helper behavior only. Ordinary `submit_run` routing has explicit drift tests that fail if either helper is invoked. Removing those helpers is a separate cleanup decision and is not part of S1B-A.

Fixed-SHA review of `8d91ee7` found and the follow-up source now addresses:

- actual runtime-provider verification before Skill/workspace preparation and `runtime.submit`;
- strict boolean permission normalization and terminal permission outcome events;
- governed pinned Skill and scoped internal context-tool exemptions without generic permission requests;
- explicit sandbox success-status allowlisting;
- placeholder cleanup degradation without deleting history;
- evidence-backed Admin container projections and counts.

## S1B-B Non-Root Blocker

Current-main process images do not establish a non-root worker/runtime contract. A safe change needs coordinated image ownership, runtime workspace permissions, provider-specific user behavior, and Docker/OpenSandbox GID handling. This slice does not change Dockerfiles, compose, `USER`, socket mounts, permissions, or deployment configuration. It does not use `chmod 777`, privileged mode, or a default Docker socket mount as a workaround.

Minimum follow-up: define and verify a dedicated runtime UID/GID across worker and executor images, workspace mounts, callback/runtime files, Docker provider startup, and OpenSandbox provider startup in a Docker-capable environment. Track that separately as S1B-B.
