# Release Authority Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a protected clean-commit release path that deploys backend and frontend from one commit and proves 211 source/image/runtime parity.

**Architecture:** A clean Git worktree produces stable required CI contexts and immutable commit-labeled images. A repository-owned deployment tool preserves the old 211 dirty tree, rejects dirty/manual inputs, runs repo-local compose, and emits one strict parity report.

**Tech Stack:** GitHub Actions, GitHub Rulesets API, Python 3.13, pytest, Docker Compose, React/Vite/pnpm, OCI labels, SSH MCP.

## Global Constraints

- Baseline source is `origin/main=d189877fde72ccffef4db3d237dba402b6029a08`.
- Never deploy from the historical dirty local lane.
- Never delete, reset, clean, or overwrite unknown 211 dirty files.
- Never copy individual source files to 211 or containers.
- Never accept a manually managed frontend container.
- Do not claim `211 verified` until strict parity succeeds.
- Do not run B1, B2, or B3 runtime acceptance.
- Every local pytest command uses a `.pytest-tmp` child.

---

### Task 1: Reproduce Current Main Failures

- [ ] Run backend focused pytest with a fresh `.pytest-tmp` child.
- [ ] Run frontend Python tests and `pnpm run ci:verify`.
- [ ] Record exact counts, tests, and root causes.
- [ ] Reconcile the requested eight failures with current evidence.

### Task 2: Repair B2 Readiness Contract

- [ ] Run each failing B2 test before editing and confirm RED.
- [ ] Make the minimal merged-source evidence contract correction.
- [ ] Rerun focused B2 tests and confirm GREEN.

### Task 3: Add Stable Required Check Contracts

- [ ] Write failing tests for unconditional `main` PR triggers and stable job names.
- [ ] Make `backend required` and `frontend required` always appear.
- [ ] Run workflow contract and YAML parsing tests.

### Task 4: Define Clean Deployment Contracts

- [ ] Add RED tests for dirty tree, commit mismatch, manual frontend, and label mismatch rejection.
- [ ] Implement source validation, redacted manifests, strict parity, and CLI commands.
- [ ] Run unit tests and CLI help smoke.

### Task 5: Integrate Frontend Into Compose

- [ ] Add RED tests for frontend ownership and shared source labels.
- [ ] Add frontend and shared commit/source labels to repo-local compose.
- [ ] Reject the standalone frontend path and validate compose on 211.

### Task 6: Preserve And Deploy 211

- [ ] Dry-run preservation and review secret exclusions.
- [ ] Create patches, archive, inventory, and hashed manifest outside checkout.
- [ ] Prepare a separate clean checkout at the merged commit.
- [ ] Build and label immutable backend/frontend images.
- [ ] Remove only the identified manual frontend after identity checks.
- [ ] Recreate all services through repo-local compose.

### Task 7: Configure Main Ruleset

- [ ] Push branch, open PR, and observe both exact contexts.
- [ ] Create active `main` Ruleset requiring PR, up-to-date branch, both checks, and blocking force pushes/deletions.
- [ ] Read back Ruleset and merge only after protection is effective.

### Task 8: Prove Same-Commit Runtime Parity

- [ ] Verify source, images, API, worker, frontend, served provenance, and compose labels.
- [ ] Confirm the preserved dirty tree remains intact.
- [ ] Record mismatches as `blocked`; only strict success permits source-authority verification.

### Task 9: Final Verification And Review

- [ ] Run targeted tests, compile, and `git diff --check`.
- [ ] Complete large-feature self-review and independent review gate.
- [ ] Refresh Ruleset, CI, preservation, compose, and parity evidence.
