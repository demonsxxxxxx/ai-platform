# B4 Skill Upload And Version Lifecycle Status

Status source for the B4 upload/import and version lifecycle continuation
slice after PR #331.

## Current Label

`local partial`

This slice does not repeat PR #329 pinned snapshot governance or PR #331
release-evidence promote gating. It advances B4-G1, B4-G2, B4-G3, and B4-G4
source/API contracts only.

## Status

- [x] Fresh branch created from current `origin/main`.
  Evidence: `codex/b4-upload-version-lifecycle-20260706` starts at
  `f4ebe1d9e1b08a94980624fafaf37b7c84236d16`.
- [x] Baseline compile check completed.
  Evidence: `python -m compileall -q app tools scripts` exited 0.
- [x] Baseline B4 focused tests completed.
  Evidence: `python -m pytest tests/test_admin_skills.py tests/test_skill_release_readiness.py tests/test_verify_governance_runtime_smoke.py -q --basetemp .pytest-tmp`
  reported 101 passed.
- [x] Baseline readiness remains blocked.
  Evidence: `python tools\skill_release_readiness.py --format json` exited 0
  and reported top-level `partial_blocked`.
- [x] Upload/import contract implemented for immutable draft versions.
  Evidence: local changed-scope bundle below includes
  `tests/test_skill_packages.py` and `tests/test_admin_skills.py`.
- [x] Version lifecycle state transitions implemented and tested.
  Evidence: local changed-scope bundle below includes
  `tests/test_skill_lifecycle.py`, `tests/test_admin_skills.py`, and
  `tests/test_repositories.py`.
- [x] Ordinary-user denial for unreviewed or disabled Skills implemented and
  tested.
  Evidence: RED/GREEN tests covered disabled marketplace file reads and
  reviewed rollout previous versions in chat/run routes.
- [x] Admin release/rollback audit evidence expanded and tested.
  Evidence: local changed-scope bundle below includes
  `tests/test_admin_skills.py`.
- [x] Dependency evidence storage/validation fields connected without 211
  runtime smoke.
  Evidence: local changed-scope bundle below includes
  `tests/test_skill_packages.py` and `tests/test_admin_skills.py`.
- [x] Local changed-scope verification completed.
  Evidence: `python -m pytest tests/test_skill_lifecycle.py tests/test_skill_packages.py tests/test_admin_skills.py tests/test_skills_marketplace_routes.py tests/test_chat_routes.py tests/test_routes.py tests/test_repositories.py -q --basetemp .pytest-tmp`
  reported 360 passed, 1 existing duplicate OpenAPI operation id warning
  after fixing the final-review gray-rollout previous-version guard, including
  explicit 0 percent rollout coverage;
  `python -m compileall -q app tools scripts` exited 0; `git diff --check`
  exited 0.
- [ ] Sub-agent review substitute posted to PR or issue.
- [ ] PR created, checked, and merged.

## Boundaries

- B4 remains `local partial` until all B4-G1 through B4-G6 evidence is complete.
- No `211 verified` label is allowed for this slice.
- No `gate closable` label is allowed from this slice alone.
- This slice does not touch B1, B3, G7, deployment, Docker, or runtime Skill
  execution on 211.
- Public and ordinary-user projections must not expose raw package bytes,
  object storage keys, `content_base64`, raw release decisions, sandbox working
  directories, or executor-private payloads.

## Target Slice

- New uploaded/imported Skill packages create immutable `skill_versions` rows
  with status `draft`.
- Admin review marks a version `reviewed` only after the existing release
  review verdict passes.
- Admin promote/rollback marks selected versions `released` and records
  release/rollback audit evidence; superseded current versions become
  `deprecated` unless disabled.
- Admin disable/deprecate can remove a version from ordinary-user use without
  deleting immutable evidence.
- Ordinary-user catalog, detail, file, chat, and run paths deny or hide
  unreviewed, disabled, and deprecated Skill versions while preserving legacy
  `active` seed compatibility.
- Dependency evidence is recorded as safe metadata in `source_json` and
  validated before review/release; this does not close runtime acceptance.
