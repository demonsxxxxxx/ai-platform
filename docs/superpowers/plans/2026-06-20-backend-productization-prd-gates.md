# Backend Productization PRD Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the backend productization PRD as the executable contract for B0-B6 gates, acceptance boundaries, status language, and reference-code intake.

**Architecture:** The PRD is the planning source. Python document-contract tests enforce key wording, stage gates, reference intake boundaries, and current-source B0 status. Runtime claims still require GitHub issue/PR review plus 211 or named-target evidence.

**Tech Stack:** Markdown docs, pytest document-contract tests, `tools/foundation_alpha_readiness.py`, GitHub issues and PRs, 211 runtime evidence when runtime behavior is claimed.

## Global Constraints

- Keep Claude Agent SDK as the execution layer.
- Do not build a second platform-level agent harness in the current stage.
- Treat SDK Agent/subagent capability as work inside one governed platform run.
- Do not claim `211 verified` or `gate closable` from a docs-only PR.
- Use narrow status labels: `local partial`, `PR ready`, `reviewed`, `merged`, `211 verified`, `gate closable`.
- Runtime work must follow issue -> branch/PR -> validation -> review -> merge -> required 211 deploy/smoke -> issue closure with evidence.
- Do not copy reference-project code, add dependencies, or add runtime services without repository, commit/tag, license/provenance, security, tests, review, rollback, and runtime evidence where applicable.

---

## File Structure

- Modify: `docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md`
  - Owns B0-B6 backend productization status, `## 4. Gate Register`, `## 5. Acceptance Boundaries By Stage`, reference-code intake rules, and beta definition of done.
- Modify: `tests/test_backend_phased_prd.py`
  - Enforces that the PRD keeps required sections, status labels, gate names, negative claims, reference repositories, and this implementation-plan link.
- Modify when B0 source/runtime status changes: `docs/operations/ai-platform-gate-status.md`
  - Records current-source blocker or evidence state without turning planning text into closure evidence.
- Modify when gate-status wording changes: `tests/test_source_authority_docs.py`
  - Enforces source-authority, B0 blocker, and stale-status wording.
- Do not modify frontend PRDs or frontend tests for backend-only PRD work.

## Task 1: B0 Current-Source Status Calibration

**Files:**
- Modify: `docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md`
- Modify: `docs/operations/ai-platform-gate-status.md`
- Modify: `tests/test_source_authority_docs.py`

**Interfaces:**
- Consumes: GitHub issue number, current `origin/main` commit, readiness JSON, and 211 source/runtime observations.
- Produces: B0 wording that identifies the active current-source blocker and refuses overclaims.

- [ ] **Step 1: Write the failing source-authority assertion**

Add or update an assertion in `tests/test_source_authority_docs.py`:

```python
for required in (
    "#138",
    "4039e4bd870d99201da4fc0f002f76f2b5c4a892",
    "runtime_rollout_required",
    "foundation_runtime_concurrency_evidence",
    "This is not `211 verified` for `4039e4b`",
):
    assert required in backend_prd_text
    assert required in gate_status_text
```

- [ ] **Step 2: Run the focused failing test**

Run:

```powershell
python -m pytest tests\test_source_authority_docs.py -q --basetemp .pytest-tmp\backend-prd-b0-status-red
```

Expected before the docs update: failure naming the missing current-source marker or stale issue wording.

- [ ] **Step 3: Update B0 status text**

Update the backend PRD and gate-status snapshot so they say:

```markdown
Issue #138 records that B0 latest-main refresh remains reopened for current
`main` `4039e4bd870d99201da4fc0f002f76f2b5c4a892` after PR #137 closed
#136 by the reviewed blocker-record path.
```

Also keep the non-expansion boundary:

```markdown
This is not `211 verified` for `4039e4b`, does not close B1/B2/B3 product gates,
does not raise production concurrency defaults, does not claim Docker sandbox
hardening, and does not enable ordinary-user multi-agent exposure.
```

- [ ] **Step 4: Run the focused source-authority test**

Run:

```powershell
python -m pytest tests\test_source_authority_docs.py -q --basetemp .pytest-tmp\backend-prd-b0-status-green
```

Expected: all selected tests pass.

## Task 2: Gate Register And Acceptance Boundaries

**Files:**
- Modify: `docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md`
- Modify: `tests/test_backend_phased_prd.py`

**Interfaces:**
- Consumes: B0-B6 stage names and the status labels defined in the PRD.
- Produces: `## 4. Gate Register` and a four-boundary acceptance model in `## 5. Acceptance Boundaries By Stage` that document-contract tests can verify.

- [ ] **Step 1: Write the gate-register assertion**

Add or keep this test block in `tests/test_backend_phased_prd.py`:

```python
for gate in (
    "B0-G1",
    "B0-G2",
    "B0-G3",
    "B0-G4",
    "B0-G5",
    "B1-G1",
    "B1-G2",
    "B1-G3",
    "B1-G4",
    "B1-G5",
    "B2-G1",
    "B2-G2",
    "B2-G3",
    "B2-G4",
    "B2-G5",
    "B2-G6",
    "B3-G1",
    "B3-G2",
    "B3-G3",
    "B3-G4",
    "B3-G5",
    "B4-G1",
    "B4-G2",
    "B4-G3",
    "B4-G4",
    "B4-G5",
    "B4-G6",
    "B5-G1",
    "B5-G2",
    "B5-G3",
    "B5-G4",
    "B5-G5",
    "B6-G1",
    "B6-G2",
    "B6-G3",
    "B6-G4",
    "B6-G5",
):
    assert gate in text
```

- [ ] **Step 2: Run the focused failing test**

Run:

```powershell
python -m pytest tests\test_backend_phased_prd.py::test_backend_prd_records_explicit_gate_register -q --basetemp .pytest-tmp\backend-prd-gates-red
```

Expected before the PRD update: failure naming the first missing gate.

- [ ] **Step 3: Update the PRD gate register**

Update `## 4. Gate Register` with gate rows for:

```text
B0-G1..B0-G5
B1-G1..B1-G5
B2-G1..B2-G6
B3-G1..B3-G5
B4-G1..B4-G6
B5-G1..B5-G5
B6-G1..B6-G5
```

Keep each row scoped to required closure evidence, not implementation preference.

- [ ] **Step 4: Update stage acceptance boundaries**

For each `### 5.x` stage, keep these four subsections:

```markdown
**Entry gate**
**Local acceptance**
**Runtime acceptance**
**Exit gate**
**Cannot claim**
```

Ensure B1 names memory provenance/export/delete/redaction, B2 names real sandbox lease/callback/egress/resource/cleanup, B3 names 10 sessions x peak 4 SDK subagents/session, B4 names immutable Skills and dependency evidence, B5 separates file/artifact ACL from exact tool decisions, and B6 requires named workflow-owner signoff.

- [ ] **Step 5: Run the focused gate tests**

Run:

```powershell
python -m pytest tests\test_backend_phased_prd.py -q --basetemp .pytest-tmp\backend-prd-gates-green
```

Expected: all backend PRD document-contract tests pass.

## Task 3: Reference Project Intake Rules

**Files:**
- Modify: `docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md`
- Modify: `tests/test_backend_phased_prd.py`

**Interfaces:**
- Consumes: Public repository owner/name references and the PRD non-goal that ai-platform owns backend authority.
- Produces: A reference matrix that supports planning without approving dependencies.

- [ ] **Step 1: Write reference-repository assertions**

Keep assertions for these repository references in `tests/test_backend_phased_prd.py`:

```python
for repo in (
    "langchain-ai/langgraph",
    "mem0ai/mem0",
    "getzep/zep",
    "getzep/graphiti",
    "OpenHands/OpenHands",
    "e2b-dev/E2B",
    "daytonaio/daytona",
    "google/gvisor",
    "kata-containers/kata-containers",
    "firecracker-microvm/firecracker",
    "anthropic-experimental/sandbox-runtime",
    "temporalio/sdk-python",
    "celery/celery",
    "Bogdanp/dramatiq",
    "taskiq-python/taskiq",
    "BerriAI/litellm",
    "Portkey-AI/gateway",
    "backstage/backstage",
    "langgenius/dify",
    "open-webui/open-webui",
    "danny-avila/LibreChat",
    "Mintplex-Labs/anything-llm",
    "openfga/openfga",
    "authzed/spicedb",
    "apache/casbin",
    "open-policy-agent/opa",
    "IBM/mcp-context-forge",
    "agentic-community/mcp-gateway-registry",
    "dockersamples/labspace-mcp-gateway",
    "goodatlas/mcp-supergateway",
    "langfuse/langfuse",
    "Arize-ai/phoenix",
    "open-telemetry/opentelemetry-collector",
    "promptfoo/promptfoo",
    "vibrantlabsai/ragas",
    "Giskard-AI/giskard-oss",
):
    assert repo in text
```

- [ ] **Step 2: Run the focused failing test**

Run:

```powershell
python -m pytest tests\test_backend_phased_prd.py::test_backend_prd_records_reference_projects_without_delegating_authority -q --basetemp .pytest-tmp\backend-prd-refs-red
```

Expected before the PRD update: failure naming the missing repository or authority boundary.

- [ ] **Step 3: Update the PRD reference section**

Update `## 7. Reference Code Projects` so each reference is bounded by:

```markdown
They do not define ai-platform authority.
Reading a project does not authorize copying code, adding dependencies, or changing runtime architecture.
Any code adaptation or runtime dependency must go through issue, license/provenance review, tests, PR review, and runtime evidence when applicable.
```

Add supply-chain intake for credentials, model keys, runtime execution, container launch, filesystem access, network egress, and package installation.

- [ ] **Step 4: Run the focused reference test**

Run:

```powershell
python -m pytest tests\test_backend_phased_prd.py::test_backend_prd_records_reference_projects_without_delegating_authority -q --basetemp .pytest-tmp\backend-prd-refs-green
```

Expected: the reference test passes.

## Task 4: Validation, PR, And Status Boundary

**Files:**
- Modify: `docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md`
- Modify: `docs/operations/ai-platform-gate-status.md`
- Modify: `tests/test_backend_phased_prd.py`
- Modify: `tests/test_source_authority_docs.py`

**Interfaces:**
- Consumes: completed docs and tests from Tasks 1-3.
- Produces: A PR that can be called `PR ready`, not `211 verified` or `gate closable`.

- [ ] **Step 1: Run current-source readiness**

Run:

```powershell
python tools\foundation_alpha_readiness.py --format json
```

Expected for a docs-only PR while current runtime is stale: `foundation_alpha_stage_status=runtime_rollout_required`.

- [ ] **Step 2: Run focused document-contract tests**

Run:

```powershell
python -m pytest tests\test_backend_phased_prd.py tests\test_source_authority_docs.py -q --basetemp .pytest-tmp\backend-prd-product-gates
```

Expected: all selected tests pass.

- [ ] **Step 3: Run compile and whitespace checks**

Run:

```powershell
python -m compileall -q app tools scripts
git diff --check
```

Expected: both commands exit 0.

- [ ] **Step 4: Commit only backend PRD scope**

Run:

```powershell
git add docs\superpowers\specs\2026-06-18-ai-platform-backend-phased-prd.md docs\operations\ai-platform-gate-status.md tests\test_backend_phased_prd.py tests\test_source_authority_docs.py docs\superpowers\plans\2026-06-20-backend-productization-prd-gates.md
git commit -m "docs: clarify backend productization gates"
```

Expected: one commit containing backend docs/tests only.

- [ ] **Step 5: Push and update the PR**

Run:

```powershell
git push
```

Expected: the existing branch updates. The PR body states this is docs/source-contract work only and does not claim `211 verified` or `gate closable`.

## Self-Review

- Spec coverage: B0-B6 stage gates, acceptance boundaries, current-source B0 blocker status, reference-code intake, and product-beta definition of done are covered.
- Placeholder scan: no `TBD`, `TODO`, or "implement later" placeholders are used.
- Type/name consistency: gate names use `B<stage>-G<number>`, status labels use the PRD's exact lowercase backtick form, and evidence levels match the PRD names.
