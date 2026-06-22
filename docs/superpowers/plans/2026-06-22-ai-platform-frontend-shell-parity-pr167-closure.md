# AI Platform Frontend Shell Parity PR167 Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining PR #167 Phase 1 frontend shell parity gap so the deployed ai-platform frontend handles `/` and `$` composer Skills flows, fail-closed chips, ordinary-user copy, and 211 smoke evidence without claiming Phase 2 backend contracts.

**Architecture:** Keep the change inside the existing `frontend/web` React shell and use current ai-platform projections only. Typed composer commands resolve through `chatInputCommands.ts`; `ChatInput.tsx` owns UI state transitions, chips, selectors, and fail-closed behavior. Deployment remains the existing 211 Python static service on port `18001`.

**Tech Stack:** React 19, TypeScript, React Router, Tailwind utility classes, `lucide-react`, Node `tsx --test`, Vite, Python compile checks, GitHub PR #167, existing 211 static deployment.

## Global Constraints

- The authenticated app must no longer visually read as LambChat or as a mixed shell.
- The authenticated chat shell must follow the approved LibreChat / poco-claw reference pattern without importing its backend authority.
- `/` opens command flows for `/skill`, `/mcp`, `/agent`, `/model`, `/file`, and `/context`; `$` opens or filters directly to Skills.
- Skills, MCP, files, artifacts, sharing, and channels must use ai-platform authority and must not expose raw paths, storage keys, provider secrets, executor-private payloads, or sandbox workdirs.
- Missing department marketplace, MCP policy assignment, share ACL, channel import, user/role/department, or model admin contracts must render explicit unavailable states.
- `/apps` remains a click-through company launchpad and must not absorb nonGMPlims business modules.
- No emoji as structural icons; use the existing `lucide-react` icon family.
- Keep cards at modest radius, avoid nested card clutter, avoid decorative hero treatment inside the authenticated app, and preserve dense enterprise scanning.
- Evidence must include component/source tests, build, projection audit where applicable, browser screenshots or browser smoke, and 211 smoke before claiming `211 verified`.
- Do not modify backend contracts, database schema, Docker compose, or deployment topology in this plan.
- Do not stage `.superpowers/`, `.codex-tmp/`, `.pytest-tmp/`, screenshot evidence, `smoke-summary.json`, `dist`, or `node_modules`.
- 211 frontend is the Python static service at `/home/xinlin.jiang/frontend-pr111-smoke/tools/serve_ai_platform_frontend.py --host 0.0.0.0 --port 18001 --root /home/xinlin.jiang/frontend-pr111-smoke/dist --api-base http://127.0.0.1:8020`.

---

## File Structure

- Modify `frontend/web/src/components/chat/__tests__/composerCommandParity.test.ts`: keep the regression tests that prove `$` maps to unavailable Skill state and typed unavailable commands must become chips before missing selectors open.
- Modify `frontend/web/src/components/chat/ChatInput.tsx`: add the missing `draft.command.unavailable` branch inside `openCommandPanel(nextValue)` and clear textarea/menu state after inserting the fail-closed chip.
- Modify this plan only if the closure scope changes; do not edit backend PRDs for this bug.

---

### Task 1: Typed Unavailable Composer Commands Fail Closed

**Files:**
- Modify: `frontend/web/src/components/chat/__tests__/composerCommandParity.test.ts`
- Modify: `frontend/web/src/components/chat/ChatInput.tsx`

**Interfaces:**
- Consumes: `resolveComposerCommandDraft(input, commandPanelAvailability): ComposerCommandDraft | null`
- Consumes: `upsertUnavailableCommandChip(command: ReturnType<typeof parseComposerCommand>): void`
- Produces: `openCommandPanel(nextValue: string): boolean` converts `$`, `/model opus`, `/mcp fetch`, or other typed unavailable commands into an unavailable composer chip before opening a selector.

- [ ] **Step 1: Keep the failing regression test**

`frontend/web/src/components/chat/__tests__/composerCommandParity.test.ts` must include:

```ts
test("typed unavailable commands fail closed before opening missing selectors", () => {
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );

  assert.match(chatInput, /draft\.command\.unavailable/);
  assert.match(chatInput, /upsertUnavailableCommandChip\(draft\.command\)/);
  assert.match(chatInput, /setInput\(""\)/);
});
```

- [ ] **Step 2: Run focused test to verify it fails**

Run:

```powershell
cd C:\aiwt\frontend-shell-parity\frontend\web
pnpm exec tsx --test src/components/chat/__tests__/composerCommandParity.test.ts
```

Expected before the production fix: FAIL in `typed unavailable commands fail closed before opening missing selectors` because `ChatInput.tsx` lacks `upsertUnavailableCommandChip(draft.command)` in `openCommandPanel`.

- [ ] **Step 3: Add the minimal production fix**

In `frontend/web/src/components/chat/ChatInput.tsx`, inside `openCommandPanel(nextValue)` immediately after `if (!draft) return false;`, add:

```ts
      if (draft.command.unavailable) {
        upsertUnavailableCommandChip(draft.command);
        setActivePanel(null);
        setCommandSearchSeed(null);
        closeSlashMenu();
        setInput("");
        setCursorPosition(0);
        requestAnimationFrame(scheduleTextareaResize);
        return true;
      }
```

Also add `closeSlashMenu`, `scheduleTextareaResize`, and `upsertUnavailableCommandChip` to the callback dependency array if they are not already present.

- [ ] **Step 4: Run focused test to verify it passes**

Run:

```powershell
cd C:\aiwt\frontend-shell-parity\frontend\web
pnpm exec tsx --test src/components/chat/__tests__/composerCommandParity.test.ts
```

Expected after the production fix: PASS with all tests in `composerCommandParity.test.ts` passing.

- [ ] **Step 5: Commit**

Run:

```powershell
git -C C:\aiwt\frontend-shell-parity add frontend/web/src/components/chat/ChatInput.tsx frontend/web/src/components/chat/__tests__/composerCommandParity.test.ts docs/superpowers/plans/2026-06-22-ai-platform-frontend-shell-parity-pr167-closure.md
git -C C:\aiwt\frontend-shell-parity commit -m "fix: fail closed typed skill commands"
```

Expected: one commit on `codex/frontend-shell-parity`.

---

### Task 2: Verify And Review PR #167 Closure

**Files:**
- Read: `frontend/web/src/components/chat/ChatInput.tsx`
- Read: `frontend/web/src/components/chat/__tests__/composerCommandParity.test.ts`
- Read: `frontend/web/src/components/chat/chatInputCommands.ts`
- Read: `docs/superpowers/specs/2026-06-19-ai-platform-chat-experience-parity-prd.md`

**Interfaces:**
- Consumes: Task 1 commit.
- Produces: verified local evidence and one subagent review report for the new diff.

- [ ] **Step 1: Run frontend changed-scope tests**

Run:

```powershell
cd C:\aiwt\frontend-shell-parity\frontend\web
pnpm exec tsx --test src/components/chat/__tests__/composerCommandParity.test.ts src/__tests__/frontendShellParityAcceptance.test.ts src/__tests__/aiPlatformBrandGuard.test.ts src/components/workbench/__tests__/workbenchShellSource.test.ts src/components/panels/__tests__/governanceSurfaceSource.test.ts src/components/share/__tests__/shareChannelFailClosedSource.test.ts src/__tests__/launchpadRoute.test.ts src/components/launchpad/__tests__/catalog.test.ts
```

Expected: PASS.

- [ ] **Step 2: Run repository compile check**

Run:

```powershell
cd C:\aiwt\frontend-shell-parity
python -m compileall -q app tools scripts
```

Expected: exit 0.

- [ ] **Step 3: Run frontend CI verification**

Run:

```powershell
cd C:\aiwt\frontend-shell-parity\frontend\web
pnpm run ci:verify
```

Expected: exit 0. Existing fast-refresh warning in `sessionImageGallery.tsx` may remain if it is non-fatal.

- [ ] **Step 4: Run diff whitespace check**

Run:

```powershell
git -C C:\aiwt\frontend-shell-parity diff --check
git -C C:\aiwt\frontend-shell-parity diff --cached --check
```

Expected: exit 0 for both commands; the staged command may report no output if nothing is staged at that moment.

- [ ] **Step 5: Request subagent review**

Dispatch a reviewer with:

```text
Review PR #167 closure diff from the previous pushed commit to HEAD.
Check that `$` and typed unavailable composer commands fail closed before opening selectors, no backend contracts are faked, and no evidence/scratch files are included.
Return Critical/Important/Minor findings with file references.
```

Expected: no Critical or Important findings before push/deploy.

---

### Task 3: Push, Clean Build, 211 Deploy, And Smoke

**Files:**
- Read: `frontend/web/dist/ai-platform-build-provenance.json`
- Do not stage: `frontend/web/dist/**`

**Interfaces:**
- Consumes: verified Task 1 commit.
- Produces: PR #167 updated branch, clean static dist, 211 active provenance, HTTP smoke, and browser smoke evidence for the final commit.

- [ ] **Step 1: Push branch**

Run:

```powershell
git -C C:\aiwt\frontend-shell-parity push origin codex/frontend-shell-parity
```

Expected: branch updates PR #167.

- [ ] **Step 2: Build from a clean worktree**

Run:

```powershell
git -C C:\Users\Xinlin.jiang\Desktop\AI-platform\ai-platform worktree add C:\aiwt\frontend-shell-parity-clean-pr167-closure codex/frontend-shell-parity
cd C:\aiwt\frontend-shell-parity-clean-pr167-closure\frontend\web
pnpm install --frozen-lockfile
pnpm run build
Get-Content -Raw dist\ai-platform-build-provenance.json
```

Expected: provenance `git.commit` equals the Task 1 commit and `git.dirty` is `false`.

- [ ] **Step 3: Upload static dist to 211**

Run locally:

```powershell
tar -czf C:\aiwt\frontend-shell-parity\.pytest-tmp\ai-platform-frontend-pr167-closure-dist.tar.gz -C C:\aiwt\frontend-shell-parity-clean-pr167-closure\frontend\web\dist .
```

Upload that archive to `s211` as:

```text
/home/xinlin.jiang/ai-platform-frontend-pr167-closure-dist.tar.gz
```

Expected: upload succeeds without adding the archive to git.

- [ ] **Step 4: Replace 211 dist and restart 18001**

Run on `s211` with `python3`:

```bash
ROOT=/home/xinlin.jiang/frontend-pr111-smoke
ARCHIVE=/home/xinlin.jiang/ai-platform-frontend-pr167-closure-dist.tar.gz
STAMP=$(date +%Y%m%d-%H%M%S)
STAGING="$ROOT/dist-staging-pr167-closure-$STAMP"
BACKUP="$ROOT/dist-backup-before-pr167-closure-$STAMP"
mkdir -p "$STAGING"
tar -xzf "$ARCHIVE" -C "$STAGING"
python3 - "$STAGING/ai-platform-build-provenance.json" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
assert p["git"]["dirty"] is False, p
print("provenance_ok", p["git"]["commit"])
PY
mv "$ROOT/dist" "$BACKUP"
mv "$STAGING" "$ROOT/dist"
PIDS=$(ps -ef | awk '/serve_ai_platform_frontend.py/ && /--port 18001/ && !/awk/ {print $2}')
if [ -n "$PIDS" ]; then kill $PIDS; sleep 1; fi
nohup python3 "$ROOT/tools/serve_ai_platform_frontend.py" --host 0.0.0.0 --port 18001 --root "$ROOT/dist" --api-base http://127.0.0.1:8020 > "$ROOT/frontend-18001.log" 2>&1 &
sleep 2
ps -ef | grep 'serve_ai_platform_frontend.py' | grep -v grep
```

Expected: one live `--port 18001` Python static service using the new dist.

- [ ] **Step 5: Run 211 HTTP smoke**

Run on `s211`:

```bash
curl -fsS -o /tmp/ai-platform-root.html -w 'root_http=%{http_code}\n' http://127.0.0.1:18001/
curl -fsS -o /tmp/ai-platform-login.html -w 'login_http=%{http_code}\n' http://127.0.0.1:18001/auth/login
curl -fsS http://127.0.0.1:8020/api/ai/health
grep -E 'AI Platform - Enterprise AI Workbench|assets/index-' /tmp/ai-platform-root.html
! grep -E 'LambChat|lambchat\.com' /tmp/ai-platform-root.html
cat /home/xinlin.jiang/frontend-pr111-smoke/dist/ai-platform-build-provenance.json
```

Expected: `root_http=200`, `login_http=200`, backend health `{"status":"ok"}`, AI Platform title present, no LambChat marker, provenance dirty false.

- [ ] **Step 6: Run browser smoke**

Use the existing browser smoke script pattern to log in with credentials from environment variables only and exercise:

```text
/auth/login
/chat
type /
type $
/skills
/marketplace
/mcp
/channels
/apps
```

Expected: no LambChat brand, slash menu visible above the composer, `$` unavailable state becomes a chip when Skills are unavailable, and screenshots or smoke JSON are produced only as local evidence artifacts.

- [ ] **Step 7: Report exact status**

Use these labels only when supported by fresh evidence:

```text
local partial
PR ready
reviewed
211 verified
gate closable
```

Expected after all Task 3 checks pass: PR #167 can be reported as `PR ready`, `reviewed`, and `211 verified`; it is not `gate closable` unless the user-approved merge/closure gate has also completed.

---

## Self-Review

**Spec coverage:** This plan covers the remaining Phase 1 composer acceptance gap for `/` and `$`, the fail-closed command chip behavior required by the chat-experience PRD, ordinary-user frontend evidence boundaries, local verification, subagent review, and 211 static runtime smoke.

**Intentional gaps:** Real department Skill policy, MCP policy assignment, share ACL expansion, channel imports, user/role/department CRUD, and model administration stay in Phase 2 backend work. This plan only verifies the Phase 1 unavailable/read-only surfaces.

**Placeholder scan:** The plan contains no TBD/TODO/fill-in placeholders. Phase 2 references are explicit deferrals from the PRD, not missing steps.

**Type consistency:** `draft.command` is a `ParsedComposerCommand`; `upsertUnavailableCommandChip` already accepts `ReturnType<typeof parseComposerCommand>`; the added branch uses existing state setters and does not add new public types.
