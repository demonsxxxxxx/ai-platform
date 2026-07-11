# Authorized Skill Task Loop Frontend

Issue: #391

Status: source slice / local partial

## Scope

Wire the reviewed public authorized Skill projection into the ordinary chat
composer. A user can search and select one Skill for one task, see its selected
version and file requirement, submit the exact nested selector, recover from
stale or denied admission without losing the draft, and continue through the
existing run cancel, progress, event, and artifact surfaces.

This slice does not change Skill administration, tenant/user Skill preference
APIs, backend admission or worker authorization, replay version selection,
multi-agent product authority, deployment, or 211 runtime state.

## Source Authority

- [x] Clean isolated worktree fetched and read back `origin/main` at
  `818cc80135a6c4d48c600bc6e9f0ef39cf47ecb1` before product writes.
- [x] Non-interactive rebase onto that SHA completed without conflict.
- [x] After backend PR #393 merged, the clean branch fetched and read back
  `origin/main` at `c854085f916748ca3c34c8a01bfc6a505b8dca5b`, confirmed
  reviewed head `4a819396343978b22270039c9b78e0959acbbe6b` as its parent,
  and rebased non-interactively without conflict.
- [x] Public DTO and `GET /api/skills/` readback confirmed required
  `expected_version`, `input_modes`, and `requires_file` fields from merged
  backend PR #390.
- [x] Composer selection consumes only that public authorized projection; it
  does not use admin/marketplace state, raw grants, `updated_at`, Skill content,
  or local enabled/disabled preferences as authority.

## Contract

- At most one task-scoped Skill is selected.
- Selected submission sends only
  `selected_skill={skill_id, expected_version}` for Skill selection; raw
  top-level `skill_id`, `enabled_skills`, and `disabled_skills` are absent.
- Without a selected Skill, existing fixed/default/session/persona request
  preferences remain unchanged.
- `skill_selection_stale` retains the prompt, completed uploads, old selected
  version, and requires an explicit selection from the refreshed catalog.
- `capability_not_authorized` clears the cached identity, refreshes the public
  list, and requires a new authorized selection without enumerating authority.
- `file_required_for_skill` blocks admission until an existing upload has
  completed; the existing attachment/file-id contract remains the only storage
  path.
- Successful admission returns a narrow accepted outcome before the existing
  SSE lifecycle completes. Cancel still uses the current real `run_id`.
- Cookie-session requests, including SSE, use explicit included credentials;
  this slice adds no browser bearer storage or raw response logging.

## Phase Status

- [x] Phase 0 - issue #391, base readback, public DTO/API readback, lightweight
  composer wireframe, scope, and TDD matrix.
- [x] Phase 1 - TDD RED: 7/19 initial contract tests failed on the missing
  public DTO flow, single-selection UI, nested request, recoverable outcome, and
  draft preservation; state RED separately failed 5/5 before reducer behavior.
  Fixed-SHA review follow-up RED failed 6/17 on full-catalog pagination,
  atomic required-file materialization, visible selected metadata, picker
  accessibility, and browser executable discovery.
- [x] Phase 2 - GREEN: public projection type, task selection reducer, exact
  request builder, recoverable admission outcome, single-select picker/chip,
  required-file preflight, explicit refresh/reconfirm, and draft lifting are
  wired into the real composer.
- [x] Phase 3 - focused frontend run passed 177/177 tests, including default
  preference fallback, true run cancel, event/artifact projection, cookie auth,
  full authorized-catalog pagination/fail-closed behavior, and composer visual
  contract regressions. A broader exploratory test found one unrelated base
  contradiction: `frontendShellParityAcceptance` forbids `/api/ai/admin` in
  `skill.ts`, while authoritative base already contains the governed admin
  upload/preview endpoints; this slice does not change that admin contract.
- [x] Phase 4 - mock-backed browser smoke passed on desktop `1440x1100` and
  mobile `390x844`: a 201-item authorized catalog placed the target Skill on
  page two; picker search/select, upload, file-required, stale full-catalog
  refresh, denied recovery, exact payload, successful submit, and artifact
  entry passed. The evidence also checks dialog labelling, Escape close/focus
  restore, explicit stale reconfirm copy, prompt/file preservation after stale
  and denied responses, task-specific removal controls, visible version/file
  metadata, 44px mobile actions, and no overlap or horizontal overflow. Local
  screenshots/evidence are under the repository's existing ignored
  `.codex-tmp/authorized-skill-browser-smoke/` root.
- [x] Phase 5 - `ci:verify` passed projection audit, PRD source smoke, eslint,
  typecheck, Vite/PWA build, and provenance; Python compile exited 0. Exact
  precommit focused tests passed 165/165; 26 slice files produced 0
  forbidden-path, secret, personal-path, root-gitignore, or diff-check hits.
  After the #393 rebase, the review-fix baseline was `541dcf1`. Focused tests
  then passed 177/177, Python compile and `ci:verify` again exited 0, and the
  desktop/mobile mock browser evidence was regenerated with status `passed`.
- [ ] Phase 6 - fixed SHA `696e551` independent code and UX reviews found four
  Important and three Minor findings. A first rebased exact-head review at
  `541dcf1` found five further Important findings: catalog refresh failure
  could erase stale identity, malformed pagination could return partial data
  or loop, terminal SSE setup failures were swallowed, stale picker summary
  copy implied a current selection, and the selected-Skill removal action was
  not task-specific/mobile-sized. Re-review at `44ef250` closed those findings
  and found two further Important issues: malformed/null public catalog wire
  responses were not fully rejected, and terminal completion could leave the
  infinite queued toast visible over succeeded playback. Both now have focused
  regressions and local fixes. The browser harness also asserts the terminal
  toast is absent and captures readable desktop/mobile succeeded artifact
  panels without `captureBeyondViewport` dropping the fixed portal layer.
  Re-review at `a11bade` then found two remaining Important gaps: catalog page
  metadata did not reject semantically impossible totals/limits, and local SSE
  setup failure/direct cancel exits could bypass the parsed-event toast
  cleanup. The public wire boundary now enforces positive limit and coherent
  page bounds while keeping name-based deduplication, and every local terminal
  exit dismisses the queue toast. Focused tests, compile, and `ci:verify` were
  rerun after these fixes. Code re-review at `41d2a7f` found that the legal
  `total=0, skills=[]` first page was still classified as incomplete; the
  collector now accepts an authorized empty catalog while continuing to reject
  an empty page before its declared total. UX re-review at that SHA reported no
  findings. Final fixed-SHA code confirmation remains pending.
- [ ] Phase 7 - ready PR, exact-head review/validation comments, and required
  CI. No merge or 211 deployment is authorized in this lane.

## Evidence Boundary

- Browser evidence is local and mock-backed. It is not browser-role, real-role,
  211, deployment, or runtime acceptance.
- Merged backend projection and local frontend validation do not close the
  Authorized Skill Task Loop runtime, B1-B6, B2, or any release gate.
- Retry/copy/resume continue to use existing backend replay contracts; this
  slice does not select or upgrade a current Skill version for replay.
