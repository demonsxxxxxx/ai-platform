# Frontend Security S2A Phase Status

Status:
- [x] Phase 0: Scope and ownership checked. Evidence: authoritative brief is the delegation contract on baseline `124a09c39290bb3bf39d9b13bd2fa1bd632a5040`; this worktree is already isolated and clean at start; the referenced triage markdown is intentionally absent from clean `origin/main` and is not required for this lane.
- [x] Phase 1: Actual write set confirmed. Evidence: the allowed production files are `frontend/web/src/components/fileLibrary/components/FileContextMenu.tsx`, `frontend/web/src/components/documents/documentPreviewSources.ts`, `frontend/web/src/components/documents/previews/ExcelPreview.tsx`, `frontend/web/src/components/chat/FileUploadButton.tsx`, `frontend/web/package.json`, and `frontend/web/pnpm-lock.yaml`; directly related frontend tests and this status file are in scope.
- [x] Phase 2: Ownership conflicts checked. Evidence: `git status --short -- <allowed files>` returned empty in this worktree before edits; no pre-existing local modifications were present on the owned paths. The delegation brief named `frontend/web/src/components/files/FileUploadButton.tsx`, but the actual repository path is `frontend/web/src/components/chat/FileUploadButton.tsx`; no second competing file exists.
- [x] Phase 3: Red tests added for active content new-tab safety, upload fail-closed behavior, and workbook parser safety boundaries. Evidence: added `frontend/web/src/components/chat/__tests__/FileUploadButton.test.ts`, `frontend/web/src/components/documents/previews/__tests__/ExcelPreview.test.ts`, and expanded `frontend/web/src/components/documents/__tests__/documentPreviewSources.test.ts` plus `frontend/web/src/components/fileLibrary/__tests__/utils.test.ts` to cover malicious HTML/SVG, spoofed MIME, permission probe failure, unsupported workbook formats, malformed workbook XML, timeout, and unpacked-size boundaries.
- [x] Phase 4: Scoped frontend implementation applied, including active-content safe handling and workbook dependency isolation from production paths. Evidence: `documentPreviewSources.ts` now fail-closes active or opaque authenticated opens to `text/plain` blobs instead of same-origin executable blobs; `FileContextMenu.tsx` now passes `fileName` into preview opening; `FileUploadButton.tsx` blocks active content by MIME/extension and cancels the whole batch on permission denial or probe failure; `ExcelPreview.tsx` no longer imports runtime `xlsx` and now parses bounded OOXML workbook previews through `jszip` with file-size, entry-size, total-uncompressed-size, sheet, row, cell, and timeout limits; `package.json` moves `xlsx` out of production dependencies.
- [x] Phase 5: Required frontend verification, review evidence, and residual-risk handoff recorded. Evidence: targeted tests, lint, typecheck, build, projection audit, prod audit review, `git diff --check`, and independent review findings are captured below; full frontend suite still has unrelated baseline failures and this lane does not claim full security closure.

## Lane Boundaries

- Allowed production scope is frontend-only and limited to the owned files above.
- Directly related frontend tests may be added or updated.
- Do not modify `app/**`, `deploy/**`, Dockerfiles, Agent Workspace, Capability Distribution, or CI workflows in this lane.
- Do not deploy to 211 and do not claim full security closure from this lane alone.

## GitHub Tracking

- Issue: `#375` - `S2A frontend: fail-close active artifact content and remove SheetJS from browser preview path`
- Branch: `codex/375-s2a-frontend-artifact-safety`
- This lane remains frontend-only; the GitHub tracking intentionally excludes backend MIME enforcement, token-storage migration, and 211 deployment work.

## Implementation Summary

- Active preview safety:
  - `openPreviewUrl(...)` now treats HTML, SVG, XML, XHTML, MHTML, and SHTML as active content by MIME or extension and opens them only as `text/plain` blob previews.
  - Authenticated preview URLs that do not carry safe metadata now fail closed to a text blob instead of receiving a same-origin executable blob tab.
  - `FileContextMenu.tsx` passes `file.file_name` to the preview resolver so extension-based decisions remain available on revealed files.
- Upload fail-closed behavior:
  - `FileUploadButton.tsx` now infers upload categories correctly when the user explicitly selects a category.
  - Client-side extension and MIME checks are used only to fail closed for active content, not to grant trust.
  - Any permission denial or permission-probe exception now aborts the whole selected batch instead of partially uploading the remainder.
  - The document accept list no longer advertises `.xml`, `.html`, or `.htm` as allowed browser uploads.
- Workbook dependency isolation:
  - `ExcelPreview.tsx` replaces runtime `xlsx` usage with a bounded `jszip` OOXML reader for `.xlsx` and `.xlsm`.
  - Browser parsing now rejects oversized files, oversized unpacked entries, excessive total unpacked workbook data, malformed workbook structure, unsupported workbook formats, and parse timeouts.
  - `xlsx@0.18.5` is no longer reachable from the production dependency graph for frontend runtime preview code in this lane.
  - Dependency record: no new production dependency was added for this lane. `jszip@^3.10.1` was already present in `dependencies` and is dual-licensed as `(MIT OR GPL-3.0-or-later)` in the installed package metadata; `xlsx@0.18.5` is `Apache-2.0` and now remains only under `devDependencies`. The production removal path is `frontend/web/src/components/documents/previews/ExcelPreview.tsx`, and the resulting build emits `ExcelPreview-*.js` (~15.1 kB in the current build) plus `excel-preview-*.css` (~1.1 kB).

## Verification Evidence

- Fresh verification rerun date: `2026-07-11` (Asia/Shanghai).
- Root compile check: PASS.
  - Command: `python -m compileall -q app tools scripts`
- Targeted changed-scope tests: PASS.
  - Command: `corepack pnpm exec tsx --test src/components/documents/__tests__/documentPreviewSources.test.ts src/components/chat/__tests__/FileUploadButton.test.ts src/components/documents/previews/__tests__/ExcelPreview.test.ts src/components/fileLibrary/__tests__/utils.test.ts`
  - Result: `55` tests passed, `0` failed.
- Lint: PASS with pre-existing unrelated warnings only.
  - Command: `corepack pnpm run lint`
  - Result: `0` errors; remaining warnings are outside this lane (`sessionImageGallery.tsx`, `RolesPanel.tsx`).
- Typecheck: PASS.
  - Command: `corepack pnpm exec tsc -b`
- Frontend build: PASS.
  - Command: `corepack pnpm run build`
  - Result: production build succeeded; Vite emitted existing large-chunk warnings only. The Excel preview bundle is now emitted as `ExcelPreview-*.js` with a separate `excel-preview-*.css` asset.
- Projection audit: PASS WITH POLICY GAPS.
  - Command: `corepack pnpm run projection:audit`
  - Result: audit status remains `pass_with_policy_gaps`, matching existing repo governance gaps outside this lane.
- Prod dependency audit: NOT GREEN GLOBALLY, but `xlsx` / SheetJS no longer appears in the production audit path.
  - Command: `corepack pnpm audit --prod`
  - Result: `19` vulnerabilities remain (`2 high`, `13 moderate`, `4 low`) in unrelated packages such as `lodash-es`, `js-cookie`, `mermaid`, `dompurify`, and `react-router`.
  - Confirmation: `corepack pnpm audit --prod 2>&1 | Select-String 'xlsx|sheetjs|SheetJS'` returned no matches.
- Full frontend test run: NOT GREEN; baseline failures remain outside this lane.
  - Command: `corepack pnpm exec tsx --test "src/**/*.test.ts" "src/**/*.test.tsx"`
  - Current fresh summary result: `835` tests total, `813` passed, `22` failed according to the test runner summary.
  - Filtered `not ok` capture on the same command currently yields `21` named failure lines, so the harness summary and line-filtered count are not perfectly aligned; this discrepancy is recorded here instead of being flattened away.
  - Prior local run on `2026-07-10` had recorded `19` failures, so the full-suite baseline drifted upward on the fresh `2026-07-11` rerun. This PR does not attempt to fix or absorb those unrelated failures.
  - Current filtered failure names still point to existing unrelated suites in brand, workbench/projection, lazy-preview, persona, message outline, scroll behavior, notification, and skills parity coverage:
    - `brand entry surfaces consume the ai-platform home authority`
    - `skills and marketplace clients use only PR177 public contracts`
    - `chat markdown rendering does not statically import CodeMirrorViewer`
    - `chat tool result items keep CodeMirrorViewer behind a lazy wrapper`
    - `chat preview hosts do not statically import heavy preview panels`
    - `project reveal items keep ProjectPreview behind a lazy wrapper`
    - `reserves persona skeleton cards while presets are loading`
    - `App uses ChatPageSkeleton for the top-level route suspense fallback`
    - `anchors floating scroll buttons to the chat input`
    - `shows the message outline only after more than three user messages`
    - `extracts user summaries and assistant markdown headings in message order`
    - `ignores headings inside fenced code blocks`
    - `extends the settle window when observed layout changes keep arriving during history finalize`
    - `keeps history bottom lock alive for late layout shifts after the first settle`
    - `does not pull back to bottom after the user leaves bottom during history settle observation`
    - `route-level forbidden state keeps the workbench shell without loading gated panels`
    - `uses the latest assistant reply as the completed notification summary`
    - `falls back to the websocket message when no assistant summary is available`
    - `skills phase one backed operations match current public contracts`
    - plus file-level failures reported for `src/components/persona/__tests__/PersonaEditorModal.ui.test.ts` and `src/components/persona/__tests__/personaAvatar.test.ts`
- Diff hygiene: PASS.
  - Command: `git diff --check`
  - Result: no whitespace or patch-format errors.

## Review Evidence

- Independent review was run on the scoped implementation.
- Fresh sub-agent re-review on `2026-07-11` returned `no findings` and verdict `ready for PR review substitute`.
- Findings addressed in-lane:
  - Added fail-closed handling for authenticated preview opens that lack safe metadata, reducing exposure from callers outside the owned write list.
  - Added entry-size and total-uncompressed-size bounds to the workbook parser to prevent large decompression paths from remaining browser-triggerable.
  - Replaced attribute-order-sensitive workbook XML parsing with attribute extraction that tolerates reordered tags.
  - Rejected unsupported workbook formats explicitly instead of attempting partial parsing.

## Known Follow-Ups Outside This Lane

- Backend upload permission and MIME enforcement remains with the later S0 lane.
- Token storage migration is explicitly out of scope here; this lane only removes the browser execution path that could expose tokens through active artifact content.
- Full frontend suite failures listed above remain outside this lane and were not modified here.
- Global frontend dependency audit remains open for unrelated packages; this lane only removes the SheetJS production-path trigger from the frontend workbook preview path.
- Auth follow-up remains required: browser token storage itself is not migrated in this lane, only the active-content execution entry that could exfiltrate same-origin credentials.
