# Chat Experience PRD Single-PR Closure Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the chat experience parity PRD through one follow-up PR that carries the remaining Phase 1B/1C frontend evidence instead of scattering the work across multiple PRs.

**Architecture:** Keep `frontend/web` as the only production frontend source. Extend the existing LibreChat-style shell and ai-platform composer components with stable smoke selectors, backed/fail-closed page states, and browser-testable workflow evidence while preserving ai-platform backend authority.

**Tech Stack:** React 19, Vite 6, TypeScript, node:test source guards, Chrome/CDP browser smoke, existing ai-platform public/admin service adapters.

## Global Constraints

- Link all remaining PRD closure work to one follow-up PR after PR #254.
- Do not reopen or mutate PR #254; it is already merged.
- Do not auto-close #81 or #82 until PRD acceptance evidence is complete.
- Do not write credentials into source, docs, logs, PR comments, or evidence.
- Do not commit `.codex/`, `.superpowers/`, `.codex-tmp/`, `.pytest-tmp/`, `dist`, `node_modules`, screenshots, tarballs, or generated smoke output.
- Keep status labels exact: `local partial`, `PR ready`, `reviewed`, `merged`, `211 verified`, and `gate closable`.
- 211 frontend remains a Python static service until issue #156 changes that runtime.

---

## Current Evidence

- PR #254 is merged and 211 verified for the bounded post-login shell/style and governance-smoke slice.
- 211 currently serves merged-main provenance commit `b68995fc954d679c22dab7977d4f7a511c1f5e07`, `dirty=false`.
- Existing tests cover shell visual convergence, frontend governance state attributes, and source-level composer command parsing.
- Existing browser smoke covers route loading for `/auth/login`, `/chat`, `/skills`, `/marketplace`, `/roles`, `/mcp`, and `/apps` using non-credential POC frontend auth setup.

## Remaining PRD Gaps

- Real company-account login smoke on the official 211 entry is not proven.
- Ordinary-user workflow smoke is not proven end-to-end: chat -> select governed Skill -> select agent/model -> attach file/context or fail closed -> submit/run or precise blocked state -> permission/artifact evidence.
- Admin workflow smoke is not proven: Admin Runtime -> Skill governance -> tool policy -> memory/governance inspection.
- Browser evidence is still missing for `/` command menu, `$` Skills shortcut menu, selected Skill chip, selected MCP chip or denied state, and file reference chip normal/denied/error state.
- Session share and channel import need either backed workflow evidence or explicit fail-closed Phase 2 unavailable/denied evidence.
- Formal review is absent for PR #254 and must be replaced by a review gate on the follow-up PR if no GitHub review is available.

## Task 1: Composer Smoke Contract

**Files:**
- Modify: `frontend/web/src/components/chat/SlashCommandMenu.tsx`
- Modify: `frontend/web/src/components/chat/ComposerChips.tsx`
- Modify: `frontend/web/src/components/chat/ChatInputAttachments.tsx`
- Modify: `frontend/web/src/components/selectors/SkillSelector.tsx`
- Modify: `frontend/web/src/components/selectors/ToolSelector.tsx`
- Test: `frontend/web/src/components/chat/__tests__/composerCommandParity.test.ts`

**Required evidence:**
- TDD red/green test proves stable browser selectors for command menu items, Skill rows, MCP rows, chips, and file references.
- `pnpm exec tsx src/components/chat/__tests__/composerCommandParity.test.ts` passes.

## Task 2: Browser Workflow Smoke

**Files:**
- Create or update an uncommitted CDP smoke helper under ignored local scratch space, or document the exact command in PR evidence without committing generated output.
- Do not commit screenshots or raw smoke logs.

**Required evidence:**
- 211 browser smoke opens `/chat`, triggers `/`, verifies `[data-composer-command-menu]` and each `[data-composer-command-item]`.
- 211 browser smoke triggers `$`, verifies `[data-composer-skill-selector]`, selects or verifies at least one `[data-composer-skill-row]`, and observes `[data-composer-chip-kind="skill"]`.
- 211 browser smoke triggers `/mcp`, verifies `[data-composer-mcp-selector]`, selects or verifies at least one `[data-composer-mcp-row]`, and observes either `[data-composer-chip-kind="mcp"]` or a denied/unavailable state.
- 211 browser smoke verifies file reference selectors by using an allowed synthetic file upload when safe, or records an explicit fail-closed no-upload/denied state.

## Task 3: Phase 1C Route Evidence

**Files:**
- Modify only if smoke selectors or fail-closed state markers are missing.

**Required evidence:**
- `/skills`, `/marketplace`, `/mcp`, `/roles`, `/channels`, `/shared/<id>` or share unavailable/denied path, and `/apps` expose `data-frontend-governance-state` or equivalent fail-closed state.
- Ordinary-user denied admin route is checked with a non-admin frontend smoke principal.
- Admin-visible governance route is checked with an admin smoke principal.

## Task 4: Review And PR Closure

**Required evidence:**
- One follow-up PR exists for this remaining PRD closure work.
- GitHub checks pass.
- Focused tests, `pnpm run ci:verify`, `python -m compileall -q app tools scripts`, and `git diff --check` pass.
- Independent review or documented maintainer-accepted review substitute is posted to the PR.
- 211 deploy and smoke evidence is posted after merge or preview deployment, with provenance matching the claimed commit.

## Completion Boundary

The follow-up PR may be labeled `PR ready` only after local verification and browser workflow evidence are posted. It may be labeled `211 verified` only after 211 provenance matches the PR or merged-main commit and HTTP/browser smoke pass. The broader PRD is `gate closable` only after #81/#82 closure evidence covers review, 211, company-account login, ordinary workflow, admin workflow, and all required visual/state evidence.
