# S1B-B Non-Root Runtime Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce fixed non-root identity `10001:10001` for API, worker, Docker executor, and OpenSandbox executor with a conservative workspace migration contract.

**Architecture:** A dedicated workspace-permissions module owns validation, migration, and the post-drop sentinel. Image and compose declare the same fixed identity. Providers share one authenticated executor process-identity probe and reject ownership or identity ambiguity before recording a lease.

**Tech Stack:** Python 3.11, FastAPI, Pydantic settings, Docker SDK, OpenSandbox SDK 0.1.13, Docker Compose, pytest.

## Global Constraints

- Runtime UID/GID is exactly `10001:10001` and is not configurable through environment files.
- Default compose has no Docker socket or privileged mode.
- Do not use chmod, mode 777, world-writable workarounds, setuid binaries, or user-provided migration paths.
- Do not modify execution boundary, Claude worker/runner, frontend, schema, repositories, queue fencing, or real deploy environment values.
- Local status cannot exceed `source/local partial`; controller owns Docker and 211 runtime acceptance.

---

### Task 1: Workspace Permission Deep Module

**Files:**
- Create: `app/runtime/sandbox/workspace_permissions.py`
- Create: `tests/test_runtime_workspace_permissions.py`

**Interfaces:**
- Produces: `RUNTIME_UID`, `RUNTIME_GID`, `RUNTIME_USER`, `RUNTIME_WORKSPACE_ROOT`, `WorkspacePermissionError`, `initialize_runtime_workspace()`.
- Consumes: only Python standard-library filesystem APIs.

- [ ] **Step 1: Write failing tests** for new root-owned migration, target-owned no-op, foreign owner, symlink, special file, cross-device entry, unsafe mode, and post-drop sentinel failures.
- [ ] **Step 2: Verify RED** with `python -m pytest tests/test_runtime_workspace_permissions.py -q --basetemp .pytest-tmp\s1b-b-red-workspace` and observe missing-module/interface failures.
- [ ] **Step 3: Implement the module** with a fixed root, descriptor-relative no-follow traversal, complete validation before mutation, exact root/target owner allowlist, ownership migration, permanent group/GID/UID drop, and sentinel I/O.
- [ ] **Step 4: Verify GREEN** with the same pytest command and observe all tests passing.

### Task 2: Image And Compose Identity Contract

**Files:**
- Modify: `Dockerfile`
- Modify: `docker-entrypoint.sh`
- Modify: `deploy/ai-platform/docker-compose.yml`
- Modify: `deploy/ai-platform/docker-compose.sandbox.yml`
- Modify: `deploy/ai-platform/.env.example`
- Modify: `tests/test_runtime_launch_script.py`
- Modify: `tests/test_source_authority_docs.py`

**Interfaces:**
- Consumes: fixed constants `10001:10001` and `python -m app.runtime.sandbox.workspace_permissions`.
- Produces: image `USER`, runtime-owned HOME/TMP/XDG directories, one-shot initializer, API/worker dependencies, and worker-only overlay socket group.

- [ ] **Step 1: Write failing launch/source-authority tests** that parse Dockerfile and Compose structurally and reject socket-default, privileged, runtime UID/GID env overrides, 777, or a general root entrypoint bypass.
- [ ] **Step 2: Verify RED** with `python -m pytest tests/test_runtime_launch_script.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\s1b-b-red-launch`.
- [ ] **Step 3: Implement image, entrypoint, compose, overlay, and template changes** exactly as approved.
- [ ] **Step 4: Verify GREEN** with the same pytest command.

### Task 3: Authenticated Executor Process Identity

**Files:**
- Modify: `app/runtime/sandbox/contracts.py` only if a private response DTO removes duplication.
- Modify: `app/runtime/sandbox/executor_app.py`
- Modify: `app/runtime/sandbox/container_provider.py`
- Modify: `app/settings.py`
- Modify: `tests/test_sandbox_executor_app.py`
- Modify: `tests/test_sandbox_container_provider.py`
- Modify: `tests/test_sandbox_contracts.py` only if the DTO is added.

**Interfaces:**
- Produces: authenticated `GET /health/runtime-identity` returning only `uid` and `gid`; provider helper requiring exact `10001:10001`.
- Consumes: existing `EXECUTOR_AUTH_HEADER` and lease-bound executor credential.

- [ ] **Step 1: Write failing endpoint tests** for missing/wrong credential and exact minimal response.
- [ ] **Step 2: Write failing Docker tests** for stat errors, UID/GID zero or mismatch, exact create user, Config.User mismatch, process identity mismatch, cleanup, and reuse mismatch.
- [ ] **Step 3: Write failing OpenSandbox tests** for absent SDK user field, exact process identity success, missing/malformed/root/mismatched identity denial, cleanup, and cached reuse revalidation.
- [ ] **Step 4: Verify RED** with `python -m pytest tests/test_sandbox_executor_app.py tests/test_sandbox_container_provider.py tests/test_sandbox_contracts.py -q --basetemp .pytest-tmp\s1b-b-red-provider`.
- [ ] **Step 5: Implement the endpoint and shared provider identity verifier** without adding a public health field or OpenSandbox user kwarg.
- [ ] **Step 6: Verify GREEN** with the same pytest command.

### Task 4: Runtime, Worker, Workspace, And Projection Regression

**Files:**
- Modify: `tests/test_sandbox_workspace_manager.py`
- Modify: `tests/test_sandbox_runtime.py`
- Modify: `tests/test_claude_agent_worker_adapter.py`
- Modify: `tests/test_admin_runtime_routes.py` only if identity evidence changes admin projection fixtures.

**Interfaces:**
- Consumes: unchanged S1B-A runtime route and existing workspace/public projection APIs.
- Produces: regression evidence for staging, marker/log/output/artifact I/O, cancel cleanup, persistent reuse safety, real provider routing, and ordinary projection redaction.

- [ ] **Step 1: Add failing regression tests** only where current coverage does not prove the approved contract.
- [ ] **Step 2: Run the focused regression slice** with workspace-local basetemp and observe expected failures before implementation changes needed by these tests.
- [ ] **Step 3: Make only contract-local corrections**; do not edit execution boundary or Claude worker/runner.
- [ ] **Step 4: Re-run the regression slice** and observe all tests passing.

### Task 5: Phase Evidence, Verification, And Review

**Files:**
- Modify: `docs/operations/2026-07-11-s1b-nonroot-runtime-identity.md`

**Interfaces:**
- Consumes: observed command output and fixed commit SHA.
- Produces: conservative Phase status, review record, PR evidence, and controller-owned runtime blockers.

- [ ] **Step 1: Run compile**: `python -m compileall -q app tools scripts`.
- [ ] **Step 2: Run focused provider/runtime/launch/worker/source-authority tests** with `--basetemp .pytest-tmp\s1b-b-final-<timestamp>`.
- [ ] **Step 3: Run `git diff --check`, changed-file scope check, and secret-pattern scan**.
- [ ] **Step 4: Complete fixed-SHA independent security and evidence reviews**, fix accepted findings with RED/GREEN tests, and re-review until no Critical or Important findings remain.
- [ ] **Step 5: Update Phase evidence**, commit exact-head docs, push, open a ready PR linked to #394 without auto-close wording, and post exact-head review substitute and validation evidence comments.
- [ ] **Step 6: Observe required CI** and report its actual state. Do not merge or claim Docker/211 acceptance.
