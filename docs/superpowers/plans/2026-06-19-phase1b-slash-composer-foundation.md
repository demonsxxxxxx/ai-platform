# Phase 1B Slash Composer Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build the first Phase 1B composer milestone: typing `/` opens a keyboard-first command menu for Skills, MCP tools, agents, models, files, and context, and selecting supported entities renders visible chips/tokens without broadening backend authority.

**Architecture:** Keep command parsing and option derivation in small pure modules under `frontend/web/src/components/chat/slashCommand*` so behavior can be tested without rendering React. Add a compact `SlashCommandMenu` and `ComposerSelectionChips` around the existing `ChatInput` while preserving current `@` persona mention and toolbar selectors. Submit selected supported entities through existing `ChatInput` callbacks and agent option payloads where safe; unsupported backend contracts show fail-closed chips or unavailable menu states.

**Tech Stack:** React 19, TypeScript, Vite, Tailwind/CSS, lucide-react, Node `tsx --test`.

## Global Constraints

- `frontend/web` remains the only production frontend source.
- New components use ai-platform service adapters and current `ChatInputProps`; no imported backend data providers from the reference project.
- `@` mention remains persona/context mention only and cannot satisfy `/` command acceptance.
- Ordinary-user MCP is visible, governed selection only; no unmanaged server lifecycle or policy writes.
- Missing backend contracts must render backed read-only or fail-closed unavailable states, not fake working UI.
- Verification must use focused frontend tests before PR readiness; runtime claims require 211 smoke separately.

---

### Task 1: Slash Command State And Option Builder

**Files:**
- Create: `frontend/web/src/components/chat/slashCommand.ts`
- Create: `frontend/web/src/components/chat/__tests__/slashCommand.test.ts`

**Interfaces:**
- Produces:
  - `SlashCommandGroup = "skill" | "mcp" | "agent" | "model" | "file" | "context"`
  - `ComposerSelectionToken`
  - `SlashCommandOption`
  - `findSlashCommandMatch(input: string, cursorPosition: number): SlashCommandMatch | null`
  - `buildSlashCommandOptions(args: BuildSlashCommandOptionsArgs): SlashCommandOption[]`
  - `applySlashCommandSelection(input: string, match: SlashCommandMatch, option: SlashCommandOption): { input: string; cursorPosition: number; token: ComposerSelectionToken | null; nextPanel: FeaturePanel }`
- Consumes existing `ToolState`, `SkillResponse`, `AgentInfo`, `AgentOption`, `FileCategory`, and `FeaturePanel`.

- [x] **Step 1: Write failing behavior tests**

Create `frontend/web/src/components/chat/__tests__/slashCommand.test.ts` with tests for:
- `/` at start activates an empty command query.
- `/ski` filters to Skills.
- `hello /mcp` activates after whitespace.
- `abc/skill` does not activate inside a word.
- command options include `/skill`, `/mcp`, `/agent`, `/model`, `/file`, `/context`.
- skill selection returns a `skill` token and removes the slash text.
- MCP tool selection returns a `mcp` token and removes the slash text.
- unavailable context returns no token and sets a `context` fail-closed state.

Run:

```powershell
Set-Location frontend/web
corepack pnpm exec tsx --test src/components/chat/__tests__/slashCommand.test.ts
```

Expected: FAIL because `slashCommand.ts` does not exist.

- [x] **Step 2: Implement pure slash command module**

Create `frontend/web/src/components/chat/slashCommand.ts` with exact exported types/functions. Keep it pure. Do not import React.

Implementation requirements:
- Activation only when `/` is at start or preceded by whitespace.
- Query ends at cursor; whitespace after `/` closes the command.
- Base command options are always present.
- Detail options derive from current props:
  - enabled Skills become `skill` options.
  - visible MCP tools are `tool.category === "mcp"` and become `mcp` options with `disabled` when not enabled or system disabled.
  - agents become `agent` options.
  - model values come from `agentOptions` select-like options and current `agentOptionValues`.
  - file categories become `file` options.
  - context is a fail-closed unavailable option until a backed projection exists.
- Applying `skill`, `mcp`, `agent`, `model`, and `file` removes the slash text and returns a token or panel action.
- Applying unavailable options removes nothing and returns no token.

- [x] **Step 3: Verify pure tests pass**

Run:

```powershell
Set-Location frontend/web
corepack pnpm exec tsx --test src/components/chat/__tests__/slashCommand.test.ts
```

Expected: PASS.

### Task 2: Slash Command UI And Composer Chips

**Files:**
- Create: `frontend/web/src/components/chat/SlashCommandMenu.tsx`
- Create: `frontend/web/src/components/chat/ComposerSelectionChips.tsx`
- Modify: `frontend/web/src/components/chat/ChatInput.tsx`
- Modify: `frontend/web/src/styles/chat.css`
- Test: `frontend/web/src/components/chat/__tests__/slashCommand.test.ts`

**Interfaces:**
- Consumes Task 1 exports.
- Produces UI visible from `ChatInput`:
  - `SlashCommandMenu` receives `options`, `highlightedIndex`, `placement`, `onHover`, `onSelect`, and `onClose`.
  - `ComposerSelectionChips` receives selected tokens and remove callbacks.

- [x] **Step 1: Extend tests for keyboard movement model**

Add pure tests to `slashCommand.test.ts` for:
- `moveSlashCommandHighlight(current, "down", optionCount)` wraps.
- `moveSlashCommandHighlight(current, "up", optionCount)` wraps.
- `dedupeComposerTokens(tokens, nextToken)` replaces tokens by `type:id`.

Run:

```powershell
Set-Location frontend/web
corepack pnpm exec tsx --test src/components/chat/__tests__/slashCommand.test.ts
```

Expected: FAIL because helpers are not implemented.

- [x] **Step 2: Implement helpers**

Add `moveSlashCommandHighlight` and `dedupeComposerTokens` to `slashCommand.ts`.

- [x] **Step 3: Add UI components and wire ChatInput**

Implement:
- `SlashCommandMenu.tsx` with lucide icons, `role="listbox"`, options as `button` rows, active state, disabled/unavailable copy, and no emoji.
- `ComposerSelectionChips.tsx` with removable token chips for selected `skill`, `mcp`, `agent`, `model`, `file`, and `context`.
- `ChatInput.tsx` state:
  - `slashHighlightedIndex`
  - `composerTokens`
  - computed `slashMatch`, `slashOptions`, and popup placement using existing `getMentionPopupFixedPlacement`.
  - key handling priority: slash menu first, then `@` mention, then normal submit/history.
  - selecting a skill calls existing `onToggleSkill` when the Skill is disabled before adding chip; if callback fails, do not add chip.
  - selecting an MCP tool calls existing `onToggleTool` when the tool is disabled and selectable; system-disabled tools show disabled/unavailable.
  - selecting an agent calls `onSelectAgent`.
  - selecting a model calls `onToggleAgentOption` for the matched select option.
  - selecting a file opens the matching upload panel/action by setting `activePanel` or relying on toolbar upload command only if direct file input cannot be safely invoked.
  - context selection shows a fail-closed token/unavailable state only when backed; otherwise no token.
  - on submit, selected `skill` and `mcp` token ids are represented in `agentOptionValues` as `selected_skill_names` and `selected_mcp_tools` if not already present, then chips clear after send.

Style with `.slash-command-*` and `.composer-token-*` classes in `chat.css`.

- [x] **Step 4: Verify focused tests and typecheck**

Run:

```powershell
Set-Location frontend/web
corepack pnpm exec tsx --test src/components/chat/__tests__/slashCommand.test.ts
corepack pnpm exec tsc -b --pretty false
```

Expected: both PASS.

### Task 3: UX Guard Tests And Documentation Evidence

**Files:**
- Create: `frontend/web/src/components/chat/__tests__/slashCommandSource.test.ts`
- Modify: `docs/superpowers/plans/2026-06-19-phase1b-slash-composer-foundation.md`

**Interfaces:**
- Produces source-level guard checks that can run in Node without a browser.

- [x] **Step 1: Write source guard tests**

Create tests that assert:
- `ChatInput.tsx` imports `SlashCommandMenu` and `ComposerSelectionChips`.
- `SlashCommandMenu.tsx` includes command group copy for Skills, MCP, agents, models, files, and context.
- `ChatInput.tsx` keeps `useMentionState` and does not remove `MentionPopup`.
- source contains `selected_skill_names` and `selected_mcp_tools` in submit path.

Run:

```powershell
Set-Location frontend/web
corepack pnpm exec tsx --test src/components/chat/__tests__/slashCommandSource.test.ts
```

Expected: PASS after Task 2; if it fails, fix implementation or test if the assertion is too brittle.

- [x] **Step 2: Run targeted verification**

Run:

```powershell
Set-Location frontend/web
corepack pnpm exec tsx --test src/components/chat/__tests__/slashCommand.test.ts src/components/chat/__tests__/slashCommandSource.test.ts src/components/chat/__tests__/chatInputViewport.test.ts
corepack pnpm exec tsc -b --pretty false
corepack pnpm run projection:audit
```

Expected:
- tests PASS,
- typecheck exits 0,
- projection audit exits 0 or the repository's known `pass_with_policy_gaps` state is explicitly reported with the policy gap detail.

- [x] **Step 3: Diff and status review**

Run:

```powershell
git diff --check
git status --short
git diff --stat
```

Expected:
- no whitespace errors,
- changed files are limited to slash composer implementation, tests, style, and this plan.

## Plan Self-Review

- Spec coverage: covers Phase 1B first practical milestone, especially `/skill`, `/mcp`, `/agent`, `/model`, `/file`, `/context`, chips/tokens, backed-or-fail-closed states, keyboard usage, and source/projection boundaries.
- Known gap: this plan does not complete full workspace visual parity, Skills hub, Marketplace, MCP admin, or company-account 211 browser smoke. Those remain later Phase 1B/1C/Phase 2 work.
- Placeholder scan: no placeholder instructions remain; unavailable backend surfaces are explicitly fail-closed.

## Execution Evidence

- 2026-06-19: Red test observed for `frontend/web/src/components/chat/__tests__/slashCommand.test.ts` before `slashCommand.ts` existed: `ERR_MODULE_NOT_FOUND`.
- 2026-06-19: Focused slash tests passed: `corepack pnpm exec tsx --test src/components/chat/__tests__/slashCommand.test.ts`.
- 2026-06-19: Source guard tests passed: `corepack pnpm exec tsx --test src/components/chat/__tests__/slashCommandSource.test.ts`.
- 2026-06-19: Combined focused tests passed: `corepack pnpm exec tsx --test src/components/chat/__tests__/slashCommand.test.ts src/components/chat/__tests__/slashCommandSource.test.ts src/components/chat/__tests__/chatInputViewport.test.ts` with 20 tests passing.
- 2026-06-19: TypeScript build passed: `corepack pnpm exec tsc -b --pretty false`.
- 2026-06-19: Projection audit exited 0 with `status: pass_with_policy_gaps`; policy gaps are existing mapped-pending-enforcement surfaces and are not closed by this slash-composer slice.
