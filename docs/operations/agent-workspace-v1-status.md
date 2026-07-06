# Agent Workspace V1 Status

Status:
- [x] Phase 0: isolated worktree created on `codex/agent-workspace-v1-20260705`; root checkout left untouched.
- [x] Phase 1: backend projection contract tests written and red-verified; `python -m pytest tests/test_agent_workspace_projection.py -q --basetemp .pytest-tmp` failed as expected with missing route 404.
- [x] Phase 2: backend projection API implemented with tenant/user/workspace scoping and public redaction; `python -m pytest tests/test_agent_workspace_projection.py -q --basetemp .pytest-tmp` passed with 2 tests.
- [x] Phase 3: frontend read-only workspace page consumes `GET /api/agent-workspace` and existing public API helpers only; route `/agent-workspace` is wired into protected App route, TabContent, sidebar rail, expanded sidebar, i18n, and SEO.
- [x] Phase 4: targeted backend/frontend verification completed locally:
  - `python -m pytest tests/test_agent_workspace_projection.py -q --basetemp .pytest-tmp` -> 2 passed.
  - `pnpm exec tsx --test src/services/api/__tests__/agentWorkspace.test.ts src/components/panels/SidebarParts/__tests__/navigationState.test.ts` -> 5 passed.
  - `pnpm exec tsc -b` -> passed.
  - `pnpm run build` -> passed; Vite reported existing large chunk warnings.
  - `python -m compileall -q app tools scripts` -> passed.
  - `git diff --check` -> passed.
- [x] Phase 5: current-session re-verification on 2026-07-05 completed in the isolated worktree with the same commands:
  - `python -m pytest tests/test_agent_workspace_projection.py -q --basetemp .pytest-tmp` -> 2 passed.
  - `pnpm exec tsx --test src/services/api/__tests__/agentWorkspace.test.ts src/components/panels/SidebarParts/__tests__/navigationState.test.ts` -> 5 passed.
  - `pnpm exec tsc -b` -> passed.
  - `python -m compileall -q app tools scripts` -> passed.
  - `pnpm run build` -> passed; Vite reported existing large chunk warnings.
  - `git diff --check` -> passed.
- [x] Phase 6: PR branch was rebased by migration, not history rewrite, onto `origin/main` as `codex/agent-workspace-v1-pr`; unrelated B1/B2/runtime commits from the original worktree base are excluded from the PR scope.
- [x] Phase 7: clean-branch re-verification on 2026-07-06 completed:
  - `python -m pytest tests/test_agent_workspace_projection.py -q --basetemp .pytest-tmp` -> 2 passed.
  - `pnpm exec tsx --test src/services/api/__tests__/agentWorkspace.test.ts src/components/panels/SidebarParts/__tests__/navigationState.test.ts` -> 5 passed.
  - `pnpm exec tsc -b` -> passed.
  - `python -m compileall -q app tools scripts` -> passed.
  - `pnpm run build` -> passed; Vite reported existing large chunk warnings.
  - `git diff --cached --check; git diff --check` -> passed.
- [x] Phase 8: sub-agent review follow-up fixes completed and locally re-verified on the clean PR branch:
  - Backend projection now requires `chat:read` and `session:read`.
  - Artifact cards are omitted unless `artifact:download` is present.
  - Pending approvals use an Agent Workspace scoped query instead of filtering through the latest-runs page.
  - Frontend artifact URLs are allowlisted to same-origin `/api/` paths.
  - `python -m pytest tests/test_agent_workspace_projection.py -q --basetemp .pytest-tmp` -> 5 passed after adding the selected-empty-session approval scope regression.
  - `python -m pytest tests/test_repositories.py -q --basetemp .pytest-tmp -k "agent_workspace_tool_permissions or list_tool_permission_inbox"` -> 2 passed, 129 deselected.
  - `pnpm exec tsx --test src/services/api/__tests__/agentWorkspace.test.ts src/components/panels/SidebarParts/__tests__/navigationState.test.ts` -> 6 passed.
  - `pnpm exec tsc -b` -> passed.
  - `python -m compileall -q app tools scripts` -> passed.
  - `pnpm run build` -> passed; Vite reported existing large chunk warnings.
  - `git diff --cached --check` and `git diff --check` -> passed.
- [x] Phase 9: follow-up sub-agent review after Important-finding fixes passed with no Critical or Important findings; final narrow re-review after the Minor regression test also passed with no Critical, Important, or Minor findings. Review assessment: ready to merge.
- [x] Phase 10: CI projection audit failure root cause fixed locally on 2026-07-06:
  - GitHub Actions run `28774129911`, job `85314111858`, failed because `pnpm run projection:audit` treated `frontend/web/src/services/api/agent.ts` redaction guard strings as active forbidden projection terms.
  - Root cause: `agent.ts` is part of the frontend sanitizer/redaction boundary but was missing from `REDACTION_GUARD_PATHS`.
  - Fix: added `frontend/web/src/services/api/agent.ts` to `REDACTION_GUARD_PATHS` in `tools/frontend_projection_audit.py`.
  - `pnpm run projection:audit` -> passed with status `pass_with_policy_gaps`; no active-browser forbidden projection violations.
  - `python -m pytest tests/test_agent_workspace_projection.py -q --basetemp .pytest-tmp` -> 5 passed.
  - `python -m pytest tests/test_repositories.py -q --basetemp .pytest-tmp -k "agent_workspace_tool_permissions or list_tool_permission_inbox"` -> 2 passed, 129 deselected.
  - `pnpm exec tsx --test src/services/api/__tests__/agentWorkspace.test.ts src/components/panels/SidebarParts/__tests__/navigationState.test.ts` -> 6 passed.
  - `pnpm exec tsc -b` -> passed.
  - `python -m compileall -q app tools scripts` -> passed.
  - `pnpm run build` -> passed; Vite reported existing large chunk warnings.
  - `git diff --check` -> passed before commit.
- [x] Phase 11: post-fix sub-agent review completed for `88926e4..8e69a83`:
  - Review found no Critical or Important findings.
  - Minor finding was this status wording underclaiming the already-created fix commit; this doc-only refresh addresses that.
  - Review assessment: ready to merge, subject to fresh GitHub checks on the latest pushed PR head.

Notes:
- UI/UX source checklist was applied against the Agent Workspace page: tokenized workbench surfaces, Lucide icons, explicit loading/degraded states, responsive two-column layout, semantic buttons, and no hardcoded runtime data.
- `ui-ux-pro-max` search script path was not available in the local skill install, so no script-generated UX report was produced.

Current status: local CI-failure fix and post-fix sub-agent review are complete on the clean PR branch. Mergeability depends on fresh GitHub checks for the latest pushed PR head. No 211 deployment has been performed.
Browser visual smoke was not run; validation is source review, targeted tests, typecheck, build, compile, and diff whitespace only.
