# Backend poco-claw Absorption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete GitHub issue #160 and PR #159 by turning the backend-only poco-claw absorption PRD into an executable phased backend plan without runtime code changes.

**Architecture:** PR #159 is the single docs/test PR for the absorption PRD and the one PR for GitHub issue #160. This plan does not implement runtime behavior; it turns accepted poco-claw concepts into future issue slices that must each pass ai-platform's issue -> branch/PR -> local verification -> review evidence -> merge loop and add 211 evidence only when the future slice makes runtime claims.

**Tech Stack:** Markdown PRD and implementation plan, pytest document-contract tests, GitHub issues and PRs, OpenAI Codex workflow guidance, ai-platform B1-B6 backend gate vocabulary.

## Global Constraints

- Do not modify frontend UI.
- Do not copy poco-claw code.
- Do not add runtime dependencies.
- Do not change backend runtime behavior in PR #159.
- Do not claim `211 verified` from this docs/test PR.
- Do not claim `gate closable` from this docs/test PR.
- Keep PR #159 as the single PR for GitHub issue #160.
- Keep Claude Agent SDK as the execution layer.
- Treat poco-claw as a reference source, not ai-platform authority.
- Use narrow status labels: `local partial`, `PR ready`, `reviewed`, `merged`, `211 verified`, and `gate closable`.

---

## File Structure

- Modify: `docs/superpowers/specs/2026-06-20-ai-platform-backend-poco-claw-absorption-prd.md`
  - Owns the backend-only poco-claw absorption contract, single-PR boundary, status labels, and plan link.
- Create: `docs/superpowers/plans/2026-06-20-ai-platform-backend-poco-claw-absorption.md`
  - This executable plan for issue #160 and future B1-B6 issue slices.
- Modify: `tests/test_backend_poco_absorption_prd.py`
  - Enforces that PR #159 stays docs/test-only, links issue #160, includes this plan, and preserves claim boundaries.
- Do not modify: `frontend/`, `app/`, `skills/`, `deploy/`, `scripts/`, `tools/`, `pyproject.toml`, or lockfiles in PR #159.

### Task 1: Complete The PRD Closure Loop

**Files:**
- Modify: `docs/superpowers/specs/2026-06-20-ai-platform-backend-poco-claw-absorption-prd.md`
- Create: `docs/superpowers/plans/2026-06-20-ai-platform-backend-poco-claw-absorption.md`
- Modify: `tests/test_backend_poco_absorption_prd.py`

**Interfaces:**
- Consumes: GitHub issue #160, PR #159, branch `codex/backend-poco-absorption-prd`, and the existing backend absorption PRD.
- Produces: One docs/test-only PR that can move from `PR ready` to `reviewed` and `merged` after verification and review evidence.

- [ ] **Step 1: Confirm the single PR loop**

Run:

```powershell
gh issue view 160 --repo demonsxxxxxx/ai-platform --json number,title,state,url
gh pr view 159 --repo demonsxxxxxx/ai-platform --json number,state,mergeStateStatus,headRefName,baseRefName,url
```

Expected: issue #160 is open, PR #159 is open against `main`, and the head branch is `codex/backend-poco-absorption-prd`.

- [ ] **Step 2: Verify the focused document-contract test is red before docs are complete**

Run:

```powershell
python -m pytest tests\test_backend_poco_absorption_prd.py -q --basetemp .pytest-tmp\poco-absorption-plan-red
```

Expected before this plan and PRD link exist: failures mention the missing implementation plan and `## 8. Implementation Plan Link`.

- [ ] **Step 3: Update the PRD closure contract**

Add this section to `docs/superpowers/specs/2026-06-20-ai-platform-backend-poco-claw-absorption-prd.md`:

```markdown
## 8. Implementation Plan Link

The executable phased plan for this PRD is
`docs/superpowers/plans/2026-06-20-ai-platform-backend-poco-claw-absorption.md`.

The plan converts the accepted poco-claw backend concepts into issue-ready B1
through B6 slices while keeping PR #159 as the only PR for GitHub issue #160.
```

- [ ] **Step 4: Run the focused green test**

Run:

```powershell
python -m pytest tests\test_backend_poco_absorption_prd.py -q --basetemp .pytest-tmp\poco-absorption-plan-green
```

Expected: all tests in `tests/test_backend_poco_absorption_prd.py` pass.

- [ ] **Step 5: Run the PRD compatibility group**

Run:

```powershell
python -m pytest tests\test_backend_phased_prd.py tests\test_source_authority_docs.py tests\test_backend_poco_absorption_prd.py -q --basetemp .pytest-tmp\poco-absorption-plan-docs
```

Expected: the backend phased PRD tests, source-authority docs tests, and poco absorption PRD tests pass together.

- [ ] **Step 6: Run compile and whitespace checks**

Run:

```powershell
python -m compileall -q app tools scripts
git diff --check
```

Expected: both commands exit 0.

- [ ] **Step 7: Update PR #159 evidence**

Run after verification:

```powershell
gh pr comment 159 --repo demonsxxxxxx/ai-platform --body "Review and verification update for issue #160: PR #159 now includes the backend absorption PRD, executable implementation plan, and document-contract tests. This remains docs/test-only and does not claim 211 verified or gate closable. Verification: python -m pytest tests\\test_backend_poco_absorption_prd.py -q --basetemp .pytest-tmp\\poco-absorption-plan-green; python -m pytest tests\\test_backend_phased_prd.py tests\\test_source_authority_docs.py tests\\test_backend_poco_absorption_prd.py -q --basetemp .pytest-tmp\\poco-absorption-plan-docs; python -m compileall -q app tools scripts; git diff --check."
```

Expected: PR #159 contains the verification and boundary evidence for issue #160.

### Task 2: Open B1 Context Snapshot Issue

**Files:**
- No PR #159 code changes. Future issue only.

**Interfaces:**
- Consumes: poco-claw session share backend contract and ai-platform B1 memory/context authority.
- Produces: A future GitHub issue for governed share/fork context snapshots. The issue is not closed by PR #159.

- [ ] **Step 1: Create the B1 issue body**

Create `.pytest-tmp\issue-b1-poco-context-snapshot.md` with this content:

```markdown
## Scope

Design and implement B1 governed share/fork context snapshots using poco-claw session share and fork concepts as reference input only.

## Acceptance criteria

- Snapshot identity binds tenant, workspace, user, run, source session, target session, and redaction state.
- Share/fork/import records provenance and rollback.
- Long-term memory remains disabled unless a separate policy gate enables it.
- Public and admin projections do not expose raw memory content, private executor payloads, backend paths, storage keys, or sandbox paths.
- Deny paths cover cross-user, cross-tenant, wrong-run, deleted/redacted memory, disabled-policy reads, and stale context-pack reads.

## Verification

- Targeted repository and route tests for snapshot creation, projection, and deny paths.
- `python -m compileall -q app tools scripts`
- `git diff --check`

## 211

Required only when the implementation claims runtime workflow evidence. Local contract work alone may claim at most `PR ready` or `reviewed`.
```

- [ ] **Step 2: Open the issue after PR #159 merges**

Run:

```powershell
gh issue create --repo demonsxxxxxx/ai-platform --title "B1 governed share and fork context snapshots" --body-file .pytest-tmp\issue-b1-poco-context-snapshot.md
```

Expected: a new issue exists and references the merged absorption PRD.

### Task 3: Open B2 Runtime Lifecycle Issue

**Files:**
- No PR #159 code changes. Future issue only.

**Interfaces:**
- Consumes: poco-claw persistent runtime registry, idle timeout, keepalive, sleep, stale detection, runtime-to-container binding, and internal executor-manager auth.
- Produces: A future GitHub issue for ai-platform-owned runtime lifecycle planning and implementation.

- [ ] **Step 1: Create the B2 issue body**

Create `.pytest-tmp\issue-b2-poco-runtime-lifecycle.md` with this content:

```markdown
## Scope

Design and implement B2 runtime lifecycle states for real sandbox execution using poco-claw persistent runtime ideas as reference input only.

## Acceptance criteria

- Runtime states include active, warm idle, sleeping, stale, stopped, failed, and deleted.
- Every state names owner, transition trigger, rollback behavior, and public/admin projection.
- Runtime identity is derived from platform-owned tenant, workspace, user, run, sandbox lease, and callback token.
- User payload cannot define runtime-to-container or runtime-to-sandbox binding.
- Missing worker, sandbox, or container state becomes stale or failed evidence rather than silent success.
- Cleanup cannot delete audit, run, artifact, lease, or release evidence.
- Internal worker/sandbox/API calls deny missing, wrong, expired, and cross-tenant internal tokens.

## Verification

- Targeted sandbox lease and runtime lifecycle tests.
- Deny-path tests for internal auth.
- Admin Runtime projection tests for stale and failed states.
- `python -m compileall -q app tools scripts`
- `git diff --check`

## 211

Required before any `211 verified` runtime lifecycle claim. Smoke must record source/runtime identity, API and worker image labels, launch, keepalive if allowed, sleep or stop, stale detection, cleanup, redaction, and reviewed evidence.
```

- [ ] **Step 2: Open the issue after PR #159 merges**

Run:

```powershell
gh issue create --repo demonsxxxxxx/ai-platform --title "B2 governed runtime lifecycle and internal executor auth" --body-file .pytest-tmp\issue-b2-poco-runtime-lifecycle.md
```

Expected: a new issue exists and requires 211 evidence for runtime claims.

### Task 4: Open B3 Capacity Evidence Issue

**Files:**
- No PR #159 code changes. Future issue only.

**Interfaces:**
- Consumes: poco-claw persistent runtime and keepalive capacity pressure lessons plus ai-platform `b3_10x4_sdk_subagents` target.
- Produces: A future GitHub issue for operator-reviewed 10 sessions x peak 4 SDK subagents capacity evidence.

- [ ] **Step 1: Create the B3 issue body**

Create `.pytest-tmp\issue-b3-poco-capacity.md` with this content:

```markdown
## Scope

Extend B3 capacity evidence so the 10 sessions x peak 4 SDK subagents per session profile includes persistent-runtime pressure and keepalive stop conditions before any default increase.

## Acceptance criteria

- The measured profile is exactly 10 concurrent sessions and peak 4 SDK subagents per session for selected workflows.
- Evidence records API burst, run creation burst, queue depth, worker start and terminal behavior, model gateway pressure, token/cost accounting, event/artifact volume, cleanup pressure, and rollback.
- If B2 persistent runtime lifecycle is enabled, evidence also records warm-idle count, keepalive duration, sleep/stop transition, stale runtime count, and sandbox/container pressure.
- Higher env values, more workers, or a fast single run do not raise production defaults.

## Verification

- Targeted queue/admission/model-gateway tests.
- Capacity harness command recorded in the issue or PR body.
- Operator-reviewed recorded snapshot.
- `python -m compileall -q app tools scripts`
- `git diff --check`

## 211

Required for any capacity or default-increase claim.
```

- [ ] **Step 2: Open the issue after PR #159 merges**

Run:

```powershell
gh issue create --repo demonsxxxxxx/ai-platform --title "B3 10x4 SDK subagent capacity with runtime pressure evidence" --body-file .pytest-tmp\issue-b3-poco-capacity.md
```

Expected: a new issue exists and explicitly blocks default increases until evidence is reviewed.

### Task 5: Open B4 Skill Reference And Group Issue

**Files:**
- No PR #159 code changes. Future issue only.

**Interfaces:**
- Consumes: poco-claw skill reference and grouped skill selection concepts.
- Produces: A future GitHub issue for immutable Skill reference resolution, group visibility, release evidence, and pinned run snapshots.

- [ ] **Step 1: Create the B4 issue body**

Create `.pytest-tmp\issue-b4-poco-skill-reference.md` with this content:

```markdown
## Scope

Implement backend Skill reference and Skill group contracts using poco-claw slash/dollar references and grouped selection as UI-driven reference input only.

## Acceptance criteria

- Skill references resolve to released immutable version IDs or digests, not mutable folder names.
- Runs record exact Skill version, manifest digest, dependency evidence reference, selected files, used-skill snapshot, and release decision.
- Disabled, unreviewed, deprecated, or mutable Skills are denied for ordinary-user runs.
- Skill groups record membership, policy, visibility, audit, dependency evidence, and batch state changes.
- Frontend batch toggles cannot become backend authority without backend policy and audit.

## Verification

- Targeted Skill route, repository, and run snapshot tests.
- Deny-path tests for disabled, unreviewed, deprecated, and mutable versions.
- `python -m compileall -q app tools scripts`
- `git diff --check`

## 211

Required for a reviewed runtime Skill run. Local API and repository contracts alone may claim at most `PR ready` or `reviewed`.
```

- [ ] **Step 2: Open the issue after PR #159 merges**

Run:

```powershell
gh issue create --repo demonsxxxxxx/ai-platform --title "B4 immutable Skill references and backend Skill groups" --body-file .pytest-tmp\issue-b4-poco-skill-reference.md
```

Expected: a new issue exists and keeps Skill authority in backend release contracts.

### Task 6: Open B5 File Share And ACL Issue

**Files:**
- No PR #159 code changes. Future issue only.

**Interfaces:**
- Consumes: poco-claw file reference and share artifact export concepts.
- Produces: A future GitHub issue for file references, selected-file snapshots, artifact ACL, redaction, retention, and share/export projection safety.

- [ ] **Step 1: Create the B5 issue body**

Create `.pytest-tmp\issue-b5-poco-file-share-acl.md` with this content:

```markdown
## Scope

Implement backend file reference and share artifact governance using poco-claw file-reference and share/export concepts as reference input only.

## Acceptance criteria

- File references resolve to ai-platform file IDs bound to tenant, workspace, user, run, retention, scan/redaction state, size, and type policy.
- Selected-file snapshots are recorded on the run and cannot be rewritten by mutable UI state.
- Artifact preview and download enforce owner/tenant ACL.
- Deny paths cover cross-user, cross-tenant, wrong-run, deleted, expired, redacted, and unauthorized artifacts.
- Share snapshots preserve provenance, redaction, retention, ACL, rollback, and export/delete posture.
- Public/admin views do not expose raw storage keys, private executor payloads, local paths, sandbox paths, secrets, or unredacted document text.

## Verification

- Targeted file route, artifact route, share snapshot, and projection-redaction tests.
- Deny-path matrix for tenant, user, run, file, artifact, retention, and redaction.
- `python -m compileall -q app tools scripts`
- `git diff --check`

## 211

Required for runtime file upload -> governed run -> artifact preview/download evidence.
```

- [ ] **Step 2: Open the issue after PR #159 merges**

Run:

```powershell
gh issue create --repo demonsxxxxxx/ai-platform --title "B5 file references, share snapshots, and artifact ACL" --body-file .pytest-tmp\issue-b5-poco-file-share-acl.md
```

Expected: a new issue exists and separates file/artifact authority from UI reference convenience.

### Task 7: Defer B6 Operations Beta Packaging

**Files:**
- No PR #159 code changes. Future issue only after B1-B5 evidence exists.

**Interfaces:**
- Consumes: completed or reviewed B1-B5 evidence.
- Produces: A deferred B6 issue only after the prerequisite runtime and governance gates have evidence.

- [ ] **Step 1: Check prerequisites before opening B6**

Run:

```powershell
gh issue list --repo demonsxxxxxx/ai-platform --state open --search "B1 B2 B3 B4 B5 in:title,body"
rg -n "B1|B2|B3|B4|B5|B6" docs\superpowers\specs\2026-06-18-ai-platform-backend-phased-prd.md docs\operations\ai-platform-gate-status.md
```

Expected: do not open a B6 beta issue unless B1-B5 evidence exists or the issue explicitly states it is a planning-only placeholder.

- [ ] **Step 2: Use this B6 issue body only after prerequisites are met**

Create `.pytest-tmp\issue-b6-poco-operations-beta.md` with this content:

```markdown
## Scope

Package one named internal workflow for Operations Beta only after B1-B5 backend evidence exists.

## Acceptance criteria

- Named workflow owner, business owner, support owner, tenant/workspace scope, SLO, expected SDK subagent fanout, cost budget, alert route, and rollback drill are recorded.
- The package links B1 memory/context, B2 sandbox, B3 capacity, B4 Skill lifecycle, and B5 file/artifact/tool-governance evidence.
- Admin Runtime, trace/export, alerting, golden-set evaluation, rollback, and support posture are operator-reviewed.
- Owner signoff is recorded.

## Verification

- Linked issue/PR/review/merge evidence for every runtime claim.
- Operator-reviewed recorded snapshot.
- 211 smoke for the named workflow if runtime behavior is claimed.

## Boundary

Do not open this as a closure issue until B1-B5 evidence is credible. A successful generated file is a controlled workflow smoke, not product beta completion.
```

- [ ] **Step 3: Open B6 only when prerequisite evidence exists**

Run:

```powershell
gh issue create --repo demonsxxxxxx/ai-platform --title "B6 operations beta package for one named workflow" --body-file .pytest-tmp\issue-b6-poco-operations-beta.md
```

Expected: a B6 issue exists only after prerequisite evidence is linked, or it is explicitly labeled as planning-only.

## Self-Review

- Spec coverage: The plan covers issue #160, PR #159, B1 context snapshots, B2 runtime lifecycle and internal auth, B3 capacity evidence, B4 Skill references and groups, B5 file/share ACL, and deferred B6 operations beta.
- Placeholder scan: The plan contains no unfinished placeholder markers.
- Boundary consistency: PR #159 remains docs/test-only and cannot claim `211 verified` or `gate closable`.
- Type and name consistency: Stage names use B1 through B6, status labels match the PRD, and future issue commands use GitHub CLI syntax already used by the repository workflow.
