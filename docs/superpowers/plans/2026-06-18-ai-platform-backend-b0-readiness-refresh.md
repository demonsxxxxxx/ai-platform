# AI Platform B0 Readiness Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the backend phased PRD entry slice by turning B0 latest-main readiness refresh into an issue/PR/runtime-evidence workflow with precise status labels.

**Architecture:** The PRD and acceptance documents define the source of truth, while `tools/foundation_alpha_readiness.py --format json` remains the operator-facing readiness contract. Runtime evidence is produced only on the 211 Docker-capable host and must bind the running API/worker image, runtime labels, source revision, health checks, smoke output, and Foundation Runtime concurrency evidence to the current merged source.

**Tech Stack:** Python readiness/verifier tools, pytest targeted docs/source-authority tests, GitHub issue/PR workflow, 211 Docker Compose deployment, release-evidence JSON/Markdown records.

---

## File Structure

- Modify: `docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md`
  - Owns backend B0-B6 sequencing, stage evidence matrix, acceptance boundaries, and reference projects.
- Modify: `docs/superpowers/specs/2026-06-10-ai-platform-product-prd-v2.md`
  - Links the backend phased PRD and preserves active product authority.
- Modify: `docs/superpowers/specs/2026-06-11-ai-platform-tech-acceptance.md`
  - Maps backend B0-B6 bundles into technical acceptance wording.
- Modify: `docs/operations/ai-platform-gate-status.md`
  - Records that the companion PRDs are planning documents, not 211 evidence.
- Create: `docs/superpowers/plans/2026-06-18-ai-platform-backend-b0-readiness-refresh.md`
  - This task plan for B0 execution.
- Potential runtime evidence output after 211 work:
  - `docs/release-evidence/foundation-alpha-poc/<current-sha>/...json`
  - `docs/release-evidence/foundation-runtime-concurrency/<current-sha>-.../...json`
  - `docs/release-evidence/README.md`

## Current Evidence Rule

B0 status must be derived from a fresh local readiness command whenever the
source changes:

```powershell
python tools\foundation_alpha_readiness.py --format json
```

A representative readiness result observed while preparing this plan showed the
same B0 blocker pattern:

```json
{
  "runtime_subject_commit_sha": "de12191b3b79b7c72e6bc2cd18f7f9ae2726f53b",
  "foundation_alpha_stage_complete": false,
  "foundation_alpha_stage_status": "runtime_rollout_required",
  "decision": {
    "runtime_rollout_required_for_current_source": true,
    "stage_acceptance_blockers": [
      "foundation_runtime_concurrency_evidence"
    ]
  }
}
```

The exact `source_tree_commit_sha` changes as PR commits are added. The
completion rule does not depend on a fixed planning-document SHA: B0 is not
`211 verified` while readiness reports `runtime_rollout_required` or lists
`foundation_runtime_concurrency_evidence` as a current-subject stage blocker.

Tracking issues:

- Docs/source-authority baseline: GitHub issue #71.
- B0 runtime and Foundation Runtime concurrency evidence refresh: GitHub issue
  #72.

## Task 1: Publish Backend Productization Baseline

**Files:**
- Create: `docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md`
- Create or include: `docs/superpowers/specs/2026-06-18-librechat-frontend-ui-absorption-prd.md`
- Modify: `docs/superpowers/specs/2026-06-10-ai-platform-product-prd-v2.md`
- Modify: `docs/superpowers/specs/2026-06-11-ai-platform-tech-acceptance.md`
- Modify: `docs/operations/ai-platform-gate-status.md`
- Create: `docs/superpowers/plans/2026-06-18-ai-platform-backend-b0-readiness-refresh.md`

- [ ] **Step 1: Create a GitHub issue for the docs baseline**

Run:

```powershell
gh issue create --title "Adopt backend phased PRD execution baseline" --body-file .tmp\issue-backend-phased-prd.md
```

Issue body:

```markdown
## Scope

Adopt the backend phased PRD and align the active PRD, technical acceptance matrix, and gate status wording so backend execution can proceed through B0-B6 with precise status labels.

## Acceptance criteria

- Backend B0-B6 sequencing is documented.
- Main PRD and technical acceptance matrix link the backend phased PRD.
- Gate status states that companion PRDs are planning/source-authority documents only.
- Current B0 readiness blocker is not hidden: latest-main runtime/FRC evidence still needs a separate 211 workflow when readiness reports `runtime_rollout_required`.
- Local docs/source-authority verification is recorded.

## Verification

- `git diff --check`
- `python -m pytest tests/test_source_authority_docs.py -q --basetemp .pytest-tmp`

## 211

Not required for this docs-only baseline. This issue must not claim `211 verified`.
```

- [ ] **Step 2: Run whitespace verification**

Run:

```powershell
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run source-authority docs tests**

Run:

```powershell
python -m pytest tests/test_source_authority_docs.py -q --basetemp .pytest-tmp
```

Expected: all tests pass.

- [ ] **Step 4: Commit docs baseline**

Run:

```powershell
git add docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md docs/superpowers/specs/2026-06-18-librechat-frontend-ui-absorption-prd.md docs/superpowers/specs/2026-06-10-ai-platform-product-prd-v2.md docs/superpowers/specs/2026-06-11-ai-platform-tech-acceptance.md docs/operations/ai-platform-gate-status.md docs/superpowers/plans/2026-06-18-ai-platform-backend-b0-readiness-refresh.md
git commit -m "docs: add backend productization execution baseline"
```

- [ ] **Step 5: Push and open PR**

Run:

```powershell
git push -u origin codex/backend-phased-prd-execution
gh pr create --base main --head codex/backend-phased-prd-execution --title "[codex] Add backend productization execution baseline" --body-file .tmp\pr-backend-phased-prd.md
```

PR body:

```markdown
## Summary

- Adds the backend phased PRD for B0-B6 backend productization.
- Links the backend and frontend companion PRDs from the active PRD, technical acceptance matrix, and gate status snapshot.
- Adds a B0 execution plan that keeps docs-only baseline work separate from 211 runtime evidence.

## Status

PR ready after local docs/source-authority verification only.

## Verification

- `git diff --check`
- `python -m pytest tests/test_source_authority_docs.py -q --basetemp .pytest-tmp`

## 211

Not run. This is a docs/source-authority baseline PR and does not claim `211 verified`.

## Follow-up

B0 latest-main runtime rollout and Foundation Runtime concurrency evidence still require a separate runtime issue/PR if readiness continues to report `runtime_rollout_required`.
```

## Task 2: Open B0 Runtime Evidence Issue

**Files:**
- No local code edits required to open the issue.

- [ ] **Step 1: Create the runtime issue**

Run:

```powershell
gh issue create --title "B0 latest-main runtime and Foundation Runtime concurrency evidence refresh" --body-file .tmp\issue-b0-runtime-refresh.md
```

Issue body:

```markdown
## Scope

Refresh B0 latest-main runtime/readiness evidence for the current merged source after the backend productization baseline lands.

## Current blocker

`tools/foundation_alpha_readiness.py --format json` reports `foundation_alpha_stage_status=runtime_rollout_required` and `stage_acceptance_blockers=["foundation_runtime_concurrency_evidence"]`.

## Acceptance criteria

- 211 source is synced to the target merged source.
- 211 API and worker containers run an image whose labels match the target merged source.
- `/api/ai/health` and relevant API/worker smoke checks pass.
- Foundation Runtime concurrency evidence is generated for the current runtime subject.
- Readiness output no longer lists `foundation_runtime_concurrency_evidence` as a current-subject stage blocker.
- Evidence is redacted and recorded under `docs/release-evidence/`.
- PR records exact commands, image tag, image ID, source SHA, runtime subject SHA, and status label.

## Verification

- Local targeted readiness/source-authority tests.
- 211 `python3` verifier commands with `--docker-cmd "sudo -n docker"` where Docker inspection is required.
- `python tools\foundation_alpha_readiness.py --format json` after evidence files are staged.

## 211

Required. This issue cannot be closed from local docs/tests alone.
```

## Task 3: Execute 211 Runtime Refresh

**Files:**
- Modify after evidence generation:
  - `docs/release-evidence/README.md`
  - `docs/release-evidence/foundation-alpha-poc/<current-sha>/...json`
  - `docs/release-evidence/foundation-runtime-concurrency/<current-sha>-.../...json`

- [ ] **Step 1: Verify 211 repository state**

Run on 211:

```bash
cd /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform
git fetch origin
git rev-parse HEAD
git rev-parse origin/main
git status --short
```

Expected: repository can be updated to the target merged source. Any dirty files must be classified before deployment.

- [ ] **Step 2: Sync 211 source**

Run on 211 only after dirty files are classified:

```bash
cd /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform
git checkout main
git pull --ff-only origin main
git rev-parse HEAD
```

Expected: output equals the target merged source SHA.

- [ ] **Step 3: Build or rebase runtime image**

Prefer normal build when dependency downloads are healthy:

```bash
cd /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform
sudo -n docker build -t ai-platform:<target-short-sha>-b0-runtime .
```

If dependency downloads fail and dependencies did not change, use the repo
AGENTS.md runtime-only rebase path and record it as a workaround.

- [ ] **Step 4: Restart compose with explicit image override**

Run:

```bash
cd /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform
sudo -n env AI_PLATFORM_IMAGE=ai-platform:<target-short-sha>-b0-runtime docker compose up -d --no-build
sudo -n docker ps --filter name=ai-platform-api --filter name=ai-platform-worker
```

Expected: API and worker containers are running the selected image.

- [ ] **Step 5: Verify health and labels**

Run on 211:

```bash
curl -fsS http://127.0.0.1:8020/api/ai/health
sudo -n docker inspect ai-platform-api ai-platform-worker --format '{{json .Config.Labels}}'
```

Expected: health status is ok and labels show the target source/runtime subject.

- [ ] **Step 6: Run Foundation Alpha smoke verifiers**

Run the repository verifier commands with `python3`, not bare `python`:

```bash
python3 tools/verify_multiuser_poc.py --base-url http://127.0.0.1:8020 --commit-sha <target-sha> --runtime-subject-commit-sha <target-sha> --image ai-platform:<target-short-sha>-b0-runtime
```

Expected: verifier exits 0 and writes redacted evidence.

- [ ] **Step 7: Run Foundation Runtime concurrency verifier**

Run:

```bash
python3 tools/verify_multiuser_poc.py --base-url http://127.0.0.1:8020 --commit-sha <target-sha> --runtime-subject-commit-sha <target-sha> --image ai-platform:<target-short-sha>-b0-runtime --foundation-runtime-concurrency
```

If this exact flag shape is not supported by current tools, inspect
`tools/verify_multiuser_poc.py --help` and use the current documented flag that
emits `ai-platform.foundation-runtime-concurrency.v1` evidence. Record the exact
command used.

- [ ] **Step 8: Bring evidence back to local source**

Copy only redacted release-evidence JSON/Markdown files into the local branch.
Do not copy `.env`, database files, logs with secrets, or raw runtime output.

- [ ] **Step 9: Verify readiness locally**

Run:

```powershell
python tools\foundation_alpha_readiness.py --format json
python -m pytest tests/test_source_authority_docs.py tests/test_foundation_alpha_readiness.py tests/test_foundation_runtime_concurrency.py -q --basetemp .pytest-tmp
git diff --check
```

Expected:
- readiness no longer lists `foundation_runtime_concurrency_evidence` as a blocker for the target current subject;
- focused tests pass;
- whitespace check passes.

- [ ] **Step 10: PR and status**

Create a runtime evidence PR linked to the B0 runtime issue. The PR can claim
`PR ready` after local verification and can claim `211 verified` only if the 211
deployment, health, labels, smoke, and concurrency evidence above were directly
observed and recorded.

## Task 4: Decide Next Backend Slice After B0

**Files:**
- No code edits unless creating the next issue.

- [ ] **Step 1: Re-read readiness and gate status after B0**

Run:

```powershell
python tools\foundation_alpha_readiness.py --format json
rg -n "G6|G7|G8|G9|B1|B2|B3|B4" docs\operations\ai-platform-gate-status.md docs\superpowers\specs\2026-06-18-ai-platform-backend-phased-prd.md
```

- [ ] **Step 2: Pick the next issue based on the blocker**

Use this decision table:

| If current blocker is | Next issue |
| --- | --- |
| memory/context workflow usability | B1 memory/context usable for one selected document workflow |
| fake sandbox remains the only production-like provider | B2 real sandbox provider smoke and hardening |
| target 10 sessions x peak 4 SDK subagents has no load evidence | B3 SDK subagent fanout capacity harness |
| Skill lifecycle is still file/image based | B4 Skills upload/version/release governance |

- [ ] **Step 3: Open the next issue with exact acceptance criteria**

The issue must state local tests, 211 requirement if runtime-affecting, review
boundary, and the narrowest status label that can be claimed.
