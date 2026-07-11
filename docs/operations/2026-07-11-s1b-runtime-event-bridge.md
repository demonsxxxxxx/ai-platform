# S1B-C Runtime Event Bridge Source Repair

Status:

- [x] Phase 0 - revision-11 envelope, scope fingerprint, worktree fingerprint, predecessor head, and exact `origin/main=c289b024e12b103ecc7eccd99fc95a6978b98dac` verified.
- [x] Phase 1 - live failure traced to the worker adapter passing a keyword-only `ExecutorEventSink` directly to `SandboxRuntime`, whose event contract calls `sink(AgentEvent)` positionally.
- [x] Phase 2 - focused RED reproduced `event_sink() takes 0 positional arguments but 1 was given` on an ordinary sandbox-required `general-chat` path.
- [x] Phase 3 - minimal local bridge reuses `agent_event_to_executor_event` and the focused regression is GREEN.
- [x] Phase 4 - focused regression passed (`1 passed`); affected worker/runtime tests passed (`125 passed, 3 skipped`); repository-root artifact count remained zero. Compile, diff, scope, and secret checks passed before commit.
- [ ] Phase 5 - exact fixed head committed, pushed, clean, and handed to the controller for a separately authorized independent review.
- [~] 211 runtime verification - retained by the controller; this lane must not access or mutate 211.

## Subject

- Controller epoch: `controller-20260711-1945`
- Board revision: `11`
- Dispatch: `dispatch-s1b-c-runtime-event-bridge-fix-20260711-g3-p2`
- Branch: `codex/397-s1bc-runtime-event-bridge`
- Base: `c289b024e12b103ecc7eccd99fc95a6978b98dac`
- Runtime evidence ceiling on entry: `runtime partial`

## Root Cause

The worker-facing `ExecutorEventSink` accepts keyword fields (`event_type`, `stage`, `message`, and `payload`). `SandboxRuntime.EventSink` accepts a single `AgentEvent` and invokes it positionally. `ClaudeAgentWorkerAdapter._submit_prepared_run_to_sandbox_runtime()` passed the worker sink directly into `SandboxRuntime.submit()`, so a real `runtime_container_started` event failed immediately after Docker lease creation.

## Repair

The adapter supplies a local async runtime sink only when a worker sink exists. It converts each `AgentEvent` through the repository's existing `agent_event_to_executor_event()` contract and forwards the result as keyword arguments. This preserves the canonical stage map, message and payload, including `admin_only` and `visible_to_user` enforcement, without changing `SandboxRuntime`, shared contracts, provider/lease behavior, persistence, worker orchestration, or deployment configuration.

## TDD Evidence

The regression uses an ordinary sandbox-required selected-Skill path. Its fake runtime follows the real interface and emits an admin-only `AgentEvent` positionally; its worker sink is explicitly keyword-only. Before the repair it raised the same live `TypeError`. After the repair it preserves the existing `skills_staged` event and forwards the runtime event as:

- `event_type=runtime_container_started`
- `stage=runtime`
- unchanged message
- original payload plus `visible_to_user=false` and `admin_only=true`

The regression now overrides `sandbox_workspace_root` to a child of its own pytest `tmp_path`, so skill staging cannot create a repository-root `s-*` directory. The exact lane-owned `s-ff88d70e` artifact from the original RED run was resolved inside this worktree and removed under revision-11 cleanup authority. A first fresh-child rerun used an overlong Windows basetemp path and failed during directory creation with `WinError 206`; a shorter fresh child under `.pytest-tmp` then passed, as did the affected suite, with no repository-root artifact recreated.

## Boundaries

This is a source repair only. It does not alter endpoint DNS handling, sandbox providers, leases, runtime contracts, schema, repositories, deployment configuration, executor images, frontend, CI, or dependencies. It does not merge, deploy, access 211, close B2/S1B, or claim runtime/gate closure.
