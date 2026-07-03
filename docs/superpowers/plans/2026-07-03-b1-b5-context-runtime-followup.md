# B1/B5 Context Runtime Follow-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for inline execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the merged bounded context foundation from source-level contracts to locally verifiable runtime/readiness evidence without touching 211.

**Architecture:** Keep the runtime follow-up narrow. Add a local machine-readable verifier for prompt/retrieval wiring, add a byte cap and failure evidence to context file staging, and document the SessionContinuity DB/Redis source-of-truth design without introducing a partial migration.

**Tech Stack:** Python, pytest, Claude Agent SDK runner seam tests, ai-platform readiness/verifier JSON contracts, Markdown operations docs.

## Global Constraints

- Work only in the isolated worktree for branch `codex/b1-b5-context-runtime-followup`.
- Do not process G7/B3, `c856`, 211 deployment, #164/#156/#81 closure, or Docker host cleanup in this PR.
- PR #307 is only `merged`; do not call it `211 verified` or `gate closable`.
- Status labels are conservative: this branch can reach only `PR ready` until reviewed/merged.
- Do not expose storage keys, private payloads, object-store locators, host paths, or private absolute paths in public projections or verifier output.
- Use TDD for behavior changes: each new behavior needs a failing test observed before production code changes.

---

## Status

- [x] Phase 0: isolated worktree created from fresh `origin/main` at `c8a2365`.
  Evidence: branch `codex/b1-b5-context-runtime-followup` was created from `origin/main`.
- [x] Phase 1: read-only context scan completed.
  Evidence: `rg "ContextManifest|SessionContinuity|context_retrieval|b1_memory_context_readiness" app tests tools docs`.
- [x] Phase 2: main checkout dirty B1 readiness files inspected read-only.
  Evidence: dirty changes are older structured B1 smoke evidence hardening and are not mixed into this branch.
- [x] Phase 3: retrieval staging guard implemented and tested.
  Evidence: `python -m pytest tests/test_context_retrieval.py -q --basetemp .pytest-tmp` passed with 9 tests.
- [x] Phase 4: B1/B5 context runtime verifier implemented and tested.
  Evidence: `python -m pytest tests/test_b1_b5_context_runtime_readiness.py -q --basetemp .pytest-tmp` passed with 3 tests.
- [x] Phase 5: SessionContinuity persistence design documented.
  Evidence: `docs/operations/b1-b5-context-runtime-follow-up.md` records SDK session resume key, fork isolation, multi-worker lock, restart recovery, and DB/Redis split.
- [x] Phase 6: compileall, focused pytest, verifier CLI, and `git diff --check` verified.
  Evidence: `python -m compileall -q app tools scripts` exited 0; `python -m pytest tests/test_context_retrieval.py tests/test_context_prompt_continuity.py tests/test_session_continuity.py tests/test_b1_b5_context_runtime_readiness.py -q --basetemp .pytest-tmp` passed with 18 tests; `python tools/verify_b1_b5_context_runtime.py --format json` exited 0 with `ok: true`; `git diff --check` exited 0.
- [x] Phase 7: post-review P2 red/green fixes completed locally.
  Evidence: `python -m pytest tests/test_context_retrieval.py::test_repository_stage_context_file_rejects_oversize_metadata_before_storage_read -q --basetemp .pytest-tmp` first failed because storage was read before oversize rejection, then passed; `python -m pytest tests/test_b1_b5_context_runtime_readiness.py::test_b1_b5_context_runtime_readiness_requires_retrieval_tools_in_allowed_tools -q --basetemp .pytest-tmp` first failed because readiness ignored `allowed_tools_include_retrieval=False`, then passed. Follow-up local checks: `python -m pytest tests/test_context_retrieval.py -q --basetemp .pytest-tmp` passed with 10 tests; `python -m pytest tests/test_b1_b5_context_runtime_readiness.py -q --basetemp .pytest-tmp` passed with 4 tests; `python -m compileall -q app tools scripts` exited 0; `python -m pytest tests/test_context_retrieval.py tests/test_context_prompt_continuity.py tests/test_session_continuity.py tests/test_b1_b5_context_runtime_readiness.py -q --basetemp .pytest-tmp` passed with 20 tests; `python tools/verify_b1_b5_context_runtime.py --format json` exited 0 with `ok: true`; `git diff --check` exited 0.

Status boundary before push: post-review fixes remain `local partial` until committed, pushed, and checked on PR #309. Do not claim `reviewed`, `merged`, `211 verified`, or `gate closable` from this local phase record.

## File Structure

- Modify `app/context_retrieval.py`: add byte cap enforcement and safe failure/audit envelope for `stage_context_file_to_workspace`.
- Modify `tests/test_context_retrieval.py`: add happy/error tests for staging cap, audit action, and private locator redaction.
- Create `app/b1_b5_context_runtime_readiness.py`: pure, local readiness builder that validates prompt, manifest, SDK tool wiring, retrieval behavior, and SessionContinuity design-document presence.
- Create `tools/verify_b1_b5_context_runtime.py`: CLI entry point emitting machine-readable JSON readiness/verifier output.
- Create `tests/test_b1_b5_context_runtime_readiness.py`: contract tests for verifier happy path, prompt leakage failure, missing retrieval-tool failure, and CLI JSON redaction.
- Create `docs/operations/b1-b5-context-runtime-follow-up.md`: operator-facing status and SessionContinuity DB/Redis source-of-truth design.

## Task 1: Add Context File Staging Guard

**Files:**
- Modify: `app/context_retrieval.py`
- Modify: `tests/test_context_retrieval.py`

**Interfaces:**
- Consumes: `ContextRetrieval.stage_context_file_to_workspace(..., workspace_root: str, max_bytes: int = 1048576)`
- Produces on success: existing safe workspace-relative path plus `audit.bytes_read`, `audit.max_bytes`, `audit.result="staged"`
- Produces on oversize: `ContextRetrievalDenied("context_file_too_large")` and no workspace write

- [x] Write failing test `test_stage_context_file_to_workspace_rejects_file_over_byte_cap_without_writing`.
- [x] Run `python -m pytest tests/test_context_retrieval.py::test_stage_context_file_to_workspace_rejects_file_over_byte_cap_without_writing -q --basetemp .pytest-tmp` and observe failure because `max_bytes` is not accepted/enforced.
- [x] Implement `max_bytes` parameter and reject raw bytes larger than the cap before creating the target directory.
- [x] Update existing staging success assertion to include the expanded audit envelope.
- [x] Run `python -m pytest tests/test_context_retrieval.py -q --basetemp .pytest-tmp`.

## Task 2: Add Local Runtime Readiness Verifier

**Files:**
- Create: `app/b1_b5_context_runtime_readiness.py`
- Create: `tools/verify_b1_b5_context_runtime.py`
- Create: `tests/test_b1_b5_context_runtime_readiness.py`

**Interfaces:**
- Consumes: local source contracts through `build_skill_prompt`, `run_claude_agent_sdk`, `ContextRetrieval`, `ContextPlanner`, and `SessionContinuity`.
- Produces: JSON schema `ai-platform.b1-b5-context-runtime-readiness.v1`.
- Required checks:
  - `chat_prompt_uses_bounded_context_manifest`
  - `document_prompt_uses_bounded_context_manifest`
  - `large_file_requires_scoped_retrieval`
  - `sdk_runner_wires_scoped_retrieval_tools`
  - `stage_context_file_byte_cap_enforced`
  - `public_projection_redacts_private_context_material`
  - `session_continuity_persistence_design_recorded`

- [x] Write failing tests that expect `build_b1_b5_context_runtime_readiness()` to return `status="local_runtime_verifier_ready"` and all required checks as passed.
- [x] Write a failing CLI test for `python tools/verify_b1_b5_context_runtime.py --format json` that rejects private terms in stdout.
- [x] Implement the pure readiness builder using in-memory repositories and fake SDK objects; do not require network, Docker, 211, or real Claude SDK.
- [x] Implement the CLI wrapper with JSON output by default and exit code `0` only when `ok` is true.
- [x] Run `python -m pytest tests/test_b1_b5_context_runtime_readiness.py -q --basetemp .pytest-tmp`.

## Task 3: Record SessionContinuity Persistence Design

**Files:**
- Create: `docs/operations/b1-b5-context-runtime-follow-up.md`
- Modify: `app/b1_b5_context_runtime_readiness.py`
- Modify: `tests/test_b1_b5_context_runtime_readiness.py`

**Interfaces:**
- Produces documented source-of-truth semantics for:
  - SDK session resume key
  - fork isolation
  - multi-worker lock
  - restart recovery
  - DB/Redis ownership split

- [x] Write failing readiness test requiring the design document to contain those five terms and status boundaries.
- [x] Create the operations document with current status, verifier commands, SessionContinuity design, retrieval boundary, and non-closure labels.
- [x] Implement doc presence checks in readiness builder.
- [x] Run `python -m pytest tests/test_b1_b5_context_runtime_readiness.py -q --basetemp .pytest-tmp`.

## Task 4: Verification

**Files:**
- No new production files.

**Interfaces:**
- Consumes all changed files.
- Produces PR-ready local verification evidence only.

- [x] Run `python -m compileall -q app tools scripts`.
- [x] Run `python -m pytest tests/test_context_retrieval.py tests/test_context_prompt_continuity.py tests/test_session_continuity.py tests/test_b1_b5_context_runtime_readiness.py -q --basetemp .pytest-tmp`.
- [x] Run `python tools/verify_b1_b5_context_runtime.py --format json`.
- [x] Run `git diff --check`.
- [x] Update this plan `Status` section with final evidence and keep status label at `local partial` or `PR ready` only if all local checks pass.
