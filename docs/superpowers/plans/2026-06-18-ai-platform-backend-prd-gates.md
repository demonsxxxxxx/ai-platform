# AI Platform Backend PRD Gate Boundary Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the backend productization PRD so B0-B6 have explicit entry gates, exit gates, blocking conditions, acceptance evidence, and reference-code boundaries.

**Architecture:** Keep the active product PRD v2 as product authority and make the backend phased PRD the backend execution contract. Add a lightweight document-contract test so future edits cannot silently remove gate wording, status labels, or reference-source boundaries.

**Tech Stack:** Markdown documentation, pytest document-contract tests, existing ai-platform source-authority docs tests.

---

### Task 1: Add Document Contract Test

**Files:**
- Create: `tests/test_backend_phased_prd.py`
- Modify: none
- Test: `tests/test_backend_phased_prd.py`

- [x] **Step 1: Write the failing test**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PRD = ROOT / "docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md"


def read_backend_prd() -> str:
    return BACKEND_PRD.read_text(encoding="utf-8")


def test_backend_prd_records_stage_gate_and_acceptance_boundaries():
    text = read_backend_prd()

    for required_section in (
        "## 3. Backend Stage Model",
        "### 3.1 Stage Evidence Matrix",
        "### 3.2 Stage Entry And Exit Gates",
        "### 3.3 Universal Blocking Conditions",
        "## 4. Gate And Acceptance Boundaries",
        "## 6. Reference Code Projects",
    ):
        assert required_section in text
```

- [x] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests\test_backend_phased_prd.py -q --basetemp .pytest-tmp\run-backend-prd-red
```

Expected: FAIL because the current PRD does not yet include the new stage-entry/stage-exit and universal-blocker sections.

### Task 2: Rewrite Backend PRD Boundaries

**Files:**
- Modify: `docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md`
- Test: `tests/test_backend_phased_prd.py`

- [x] **Step 1: Add B0-B6 entry and exit gates**

Add `### 3.2 Stage Entry And Exit Gates` after the stage evidence matrix. Each row must use the exact label format `B0 entry`, `B0 exit`, through `B6 entry`, `B6 exit`.

- [x] **Step 2: Add universal blocking conditions**

Add `### 3.3 Universal Blocking Conditions` with blockers for open issue evidence, docs-only claims, fake sandbox, configuration-only capacity changes, long-term memory exposure, mutable Skill folders, broad tool permissions, private projection leakage, and unverified 211 state.

- [x] **Step 3: Expand reference projects**

Update `## 6. Reference Code Projects` so references cover memory/context, sandbox, worker, model gateway, authorization/policy, observability, Skills management, and frontend-adjacent UX without delegating ai-platform authority.

### Task 3: Verify Documentation Contract

**Files:**
- Test: `tests/test_backend_phased_prd.py`
- Test: `tests/test_source_authority_docs.py`

- [x] **Step 1: Run backend PRD contract test**

Run:

```powershell
python -m pytest tests\test_backend_phased_prd.py -q --basetemp .pytest-tmp\run-backend-prd-green
```

Expected: PASS.

- [x] **Step 2: Run source authority docs regression**

Run:

```powershell
python -m pytest tests\test_source_authority_docs.py -q --basetemp .pytest-tmp\run-backend-prd-source-docs
```

Expected: PASS.

- [x] **Step 3: Run Markdown whitespace check**

Run:

```powershell
git diff --check
```

Expected: no whitespace errors.
