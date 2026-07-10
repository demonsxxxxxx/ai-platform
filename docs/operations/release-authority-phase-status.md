# Release Authority Phase Status

Status: `current implementation baseline`

This document tracks only Release Authority recovery. It does not authorize or
record B1, B2, or B3 runtime acceptance.

## Subjects

| Subject | Value | Evidence state |
| --- | --- | --- |
| Baseline GitHub source | `d189877fde72ccffef4db3d237dba402b6029a08` | `current` |
| Recovery worktree | `C:/aiwt/release-authority-d189877-20260710` | `current`, clean at creation |
| Recovery branch | `codex/release-authority-d189877-20260710` | `current` |
| Historical local lane | dirty `6a77c3795d5880a628fa11300a2e450e493023fb` | `historical`; untouched |
| 211 source | dirty `12d626203ba37ce724d20e579bac2ac763e1341a` | `stale`; preserved, not deployment source |
| 211 API/worker | historical ordinary-sandbox image | `stale` |
| 211 frontend | manually managed main-derived image | `stale ownership` |
| GitHub protection | no branch protection; no Ruleset | `blocked` |

## Phase Matrix

- [x] Phase 0: Baseline authority identified. Evidence: `origin/main` is `d189877fde72ccffef4db3d237dba402b6029a08`; GitHub has no protection or Ruleset.
- [x] Phase 1: Clean recovery lane created from `origin/main`; initial status was clean.
- [x] Phase 2: Current focused failures reproduced and repaired. Evidence: clean
  `d189877` backend suite reproduced `3 failed, 303 passed`; frontend Python
  suite reproduced `3 failed, 117 passed`. The six tests contained eight stale
  assertions that treated historical B1/B2 evidence as current. The corrected
  fail-closed suites now pass `306` backend tests and `129` frontend/release
  tests; frontend projection, source smoke, lint, typecheck, and build also exit 0.
- [x] Phase 3: Stable workflow contexts implemented locally as `backend required`
  and `frontend required`; GitHub PR observation and Ruleset enforcement remain pending.
- [x] Phase 4: `tools/release_authority.py` implements clean-source rejection,
  immutable image tags, image-label validation, preservation, repo-local compose
  deployment, manual frontend rejection, and strict parity reporting. Local CLI
  and contract tests pass; Docker execution remains pending on 211.
- [x] Phase 5: Frontend is defined only in repo-local
  `deploy/ai-platform/docker-compose.yml`; the standalone frontend compose file
  is removed in the recovery branch. Runtime ownership transition remains pending.
- [x] Phase 6: 211 dirty source preserved outside checkout. Evidence directory:
  `/home/xinlin.jiang/ai-platform-phaseb/release-authority-preservation/20260710T014804Z-12d626203ba37ce724d20e579bac2ac763e1341a`.
  Manifest verified five artifacts: `status.txt`, `tracked.patch`, `staged.patch`,
  `inventory.json`, and `untracked.tar`. Inventory count is `22`; untracked count
  is `1`. `tracked.patch` SHA-256 is
  `9b8fc2b9742252fe33f0d701ac88dfc9405465be747acda7860a22378c99ce72`.
  Post-preservation readback showed the same HEAD and the same dirty path list;
  no source file was cleaned, reset, deleted, or overwritten.
- [ ] Phase 7: GitHub Ruleset active with exact required checks.
- [ ] Phase 8: One merged commit deployed across source, API, worker, frontend, and image labels.
- [ ] Phase 9: Strict parity evidence reports `verified: true`.

## Current 211 Dirty Inventory

Observed paths are evidence of dirty state, not permission to alter or delete:

- Backend/runtime: `app/b2_sandbox_readiness.py`, `app/executors/claude_agent_worker.py`, `app/routes/runtime_callbacks.py`, `app/runtime/sandbox/contracts.py`, `app/runtime/sandbox/executor_app.py`, `app/runtime/sandbox/runtime.py`, `app/worker.py`.
- Deployment/template: `deploy/ai-platform/.env.example`, `scripts/generate_sandbox_runtime_evidence_211.py`, `scripts/verify_sandbox_runtime_211.py`.
- Binary assets under `assets/ai-platform-architecture-illustrations/`, `frontend/web/public/`, and two observed skill `.docx` files.
- Untracked marker: `.ai-platform-source-tree-commit`.

The authoritative manifest, hashes, patch, and archive remain pending. Until
they verify, 211 source transition is blocked.

## Prohibited Claims And Actions

- Do not state `211 verified` while Phase 9 is incomplete.
- Do not run B1, B2, or B3 runtime acceptance here.
- Do not clean, reset, delete, or overwrite the 211 dirty tree.
- Do not deploy by copying source files or patching containers.
- Do not retain a manually managed frontend container as accepted evidence.

## Local Verification Evidence

- Backend required scope: `315 passed`.
- Frontend/release required scope: `129 passed`.
- Cross-cutting pre-commit scope: `136 passed`.
- `python -m compileall -q app tools scripts`: exit `0`.
- `corepack pnpm run ci:verify`: exit `0`; projection audit, source smoke,
  ESLint, TypeScript build, Vite/PWA build, and provenance generation completed.
- `git diff --check`: exit `0`.

## Pre-Commit Self-Review

- [x] No secrets, real `.env` values, or personal paths in changed files.
- [x] New public functions/classes have docstrings.
- [x] Happy path and error path tests cover clean commit, preservation, manual
  frontend rejection, commit mismatch, and successful parity.
- [x] This Phase status and the implementation plan record the Release Authority milestone work.
