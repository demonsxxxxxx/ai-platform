# Backend Productization Gates PRD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the backend productization PRD so S2/S3 backend work has unambiguous gates, acceptance boundaries, evidence levels, and reference-project intake rules.

**Architecture:** Keep the active product PRD v2 as global authority and make `2026-06-18-ai-platform-backend-phased-prd.md` the backend execution contract. The update is docs-only: it does not create runtime claims, does not require 211 smoke, and must preserve source-authority wording across the technical acceptance matrix and gate status snapshot.

**Tech Stack:** Markdown source documents, repository source-authority tests, readiness CLI output, GitHub issue/PR workflow.

---

### Task 1: Establish Current-State Inputs

**Files:**
- Read: `docs/superpowers/specs/2026-06-10-ai-platform-product-prd-v2.md`
- Read: `docs/superpowers/specs/2026-06-11-ai-platform-tech-acceptance.md`
- Read: `docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md`
- Read: `docs/operations/ai-platform-gate-status.md`
- Read: `docs/operations/ai-platform-governance-readiness.md`

- [x] **Step 1: Confirm branch and clean isolated worktree**

Run:

```powershell
git status --short --branch
```

Expected: branch `codex/backend-prd-productization-gates` is clean before edits.

- [x] **Step 2: Confirm current readiness posture**

Run:

```powershell
python tools\foundation_alpha_readiness.py --format json
```

Expected: output distinguishes `runtime_relevant_source_verified_by_running_runtime`, `current_source_verified_by_running_runtime`, `foundation_alpha_stage_complete`, and blocked expansions.

### Task 2: Rewrite Backend PRD Gate Contract

**Files:**
- Modify: `docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md`

- [x] **Step 1: Rename the four P0 items**

Change "The four P0 backend capabilities are" to "The first four backend productization priorities are" so the document does not conflict with G0-G10 gate language.

- [x] **Step 2: Add evidence-level definitions**

Add a compact evidence ladder under `Required Evidence Shape`:

```markdown
| Evidence level | Meaning | Cannot close |
| --- | --- | --- |
| `source_contract` | Code, tests, or verifier contract define expected behavior. | Runtime acceptance. |
| `source_probe_on_target_runtime` | A target host can import or inspect the source path, but no live governed run proves it. | 211 acceptance. |
| `controlled_live_probe` | A controlled verifier proves a bounded path on a target runtime. | Broad production hardening. |
| `live_worker_run_payload` | A real platform run/worker payload proves the governed path. | Broader stage closure without issue/review/rollback evidence. |
| `live_platform_probe` | Platform API/worker/sandbox state proves a live target resource lifecycle. | Source-regression-only controls. |
| `operator_reviewed_recorded_snapshot` | Reviewed, redacted, source-bound evidence snapshot accepted by operators. | Product beta without workflow owner signoff. |
```

- [x] **Step 3: Tighten B1**

Make the B1 minimum product slice a named document workflow with governed context-pack use. Require `context_pack_version`, `context_pack_generated_at`, safe provenance, redaction, export/delete boundary, rollback, and long-term memory fail-closed invariants.

- [x] **Step 4: Tighten B2**

Define sandbox runtime evidence levels and state that standalone verifier success or worker-process task execution does not prove the governed SDK Skill path. Keep fake provider local/test-only.

- [x] **Step 5: Tighten B3**

Define the 10 sessions x peak 4 SDK subagents/session profile as an operator-reviewed recorded snapshot with tenant mix, p95/p99, error budget, stop conditions, cleanup, token/cost ledger, event/artifact volume, sandbox pressure, and rollback.

- [x] **Step 6: Split B5 internally**

Keep one B5 stage but split acceptance into B5a file/artifact authority and B5b exact tool decision governance so file ACL evidence cannot close shell/MCP replay denial.

- [x] **Step 7: Tighten B6**

Require 1-2 named internal workflows, owner, SLO, expected subagent fanout, cost budget, quality gate, alert route, support owner, rollback drill, and B1-B5 evidence links before beta readiness claims.

### Task 3: Update Reference-Code Section

**Files:**
- Modify: `docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md`

- [x] **Step 1: Add verified repository metadata**

Record GitHub repository references, URLs, default branch, license posture, and use-only-for boundaries for memory, sandbox, capacity/model gateway, and Skills management references.

- [x] **Step 2: Add intake safeguards**

State that repositories with `Other` or copyleft licenses are concept-only unless a separate license/provenance issue approves bounded code adaptation.

### Task 4: Verify Docs-Only Change

**Files:**
- Test: `tests/test_source_authority_docs.py`

- [x] **Step 1: Run source authority docs tests**

Run:

```powershell
python -m pytest tests\test_source_authority_docs.py -q --basetemp .pytest-tmp\backend-prd-productization-gates
```

Expected: pass.

- [x] **Step 2: Run markdown/diff checks**

Run:

```powershell
git diff --check
```

Expected: pass with no whitespace errors.

- [x] **Step 3: Review diff for status overclaim**

Run:

```powershell
git diff -- docs\superpowers\specs\2026-06-18-ai-platform-backend-phased-prd.md
```

Expected: no statement claims `211 verified`, `gate closable`, S2 complete, or beta ready from this docs-only update.
