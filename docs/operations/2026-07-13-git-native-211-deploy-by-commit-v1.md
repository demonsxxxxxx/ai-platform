# Git-Native 211 Deployment By Commit V1 Phase Status

Lane: `git-native-211-deploy-by-commit-v1-fix`, generation 2, phase 2.

Branch: `codex/git-native-211-deploy-by-commit-v1`.

Base: `678d3c46f7b7dc41cf5d99d5b7898551f1bb50f3`.

Issue: `https://github.com/demonsxxxxxx/ai-platform/issues/415`.

## Issue #420 Ordered Compose Set

Lane: `release-authority-compose-overlay-fix`, generation 2, phase 2.

Branch: `codex/420-release-authority-compose-overlay`.

Base: `110e047245c36237b92aa4bd32f9988b42753392`.

Issue: `https://github.com/demonsxxxxxx/ai-platform/issues/420`.

- [x] Revision-133 scope and worktree fingerprints matched the declared clean,
  detached exact-main source before mutation.
- [x] Focused RED reproduced the missing ordered selection, two-file ownership,
  deployment command, CLI forwarding, and static low-level error contract.
- [x] The existing release-authority module now validates one ordered
  repo-relative Compose set, defaults to the canonical singleton, checks exact
  API/worker/frontend ownership before image handling, and reuses the same set
  for Compose up and live parity.
- [x] Current module evidence: the focused contract passed (`19 passed`) and
  the final complete release-authority suite passed (`97 passed`).
- [x] Final local gates: source-authority docs passed (`50 passed`), compile and
  all three CLI help smokes exited 0, and diff, exact three-path scope, and
  added-lines secret/personal-path checks passed. Self-review found no real
  secret/env output, missing public docstring, uncovered primary error path,
  out-of-scope change, or unsupported closure claim.
- [x] Generation-1 publication: commit `fcf7c55432989adfd1e862cadf102451a2d6b83b`
  is pushed, draft PR `https://github.com/demonsxxxxxx/ai-platform/pull/421`
  is open, exact-head evidence was read back, and all four checks succeeded.
- [x] Revision-136 gate: generation-2 scope and worktree fingerprints matched
  the exact clean PR head and base before mutation.
- [x] Revision-135 findings intake: one independent Important and one
  controller-supplemental Important identified prior-release absolute-root
  rotation and mutable-name manual frontend removal defects.
- [x] Generation-2 RED: two focused regressions reproduced both findings on
  `fcf7c554` (`2 failed`, `97 deselected`).
- [x] Generation-2 GREEN: trusted archive-style prior siblings now retain the
  exact ordered relative Compose set without requiring `.git`; all managed
  services share one ownership root, post-deploy parity remains target-exact,
  and manual frontend removal revalidates and removes an immutable container
  ID. Focused findings tests passed (`11 passed`) and the complete suite passed
  (`107 passed`).
- [x] Generation-2 local gates: source-authority docs passed (`50 passed`),
  compile and all three CLI help smokes exited 0, and diff, exact three-path
  scope, and added-lines secret/personal-path checks passed. Self-review found
  no metadata weakening, prior-root exception in post-deploy parity, mutable
  name removal, real secret/env output, or out-of-scope change.
- [ ] Generation-2 pre-commit, follow-up publication, exact-head evidence,
  terminal CI, and fresh independent re-review remain.

Evidence ceiling: `PR open / Not Ready pending fresh independent re-review`.
This source-only lane does not access Docker, a real env, credentials, browser,
or 211; it does not deploy, merge, request review, close #415/#409, or claim
B2, S1B, G0, Agent, runtime, or gate closure.

## Status

- [x] Provisioning: clean detached worktree matched exact `origin/main`.
- [x] Envelope: revision 105 fields were echoed before mutation; scope and
  worktree fingerprints independently recomputed to the declared values.
- [x] Design: extend the existing release-authority module and reuse its deploy
  and live-parity functions; no parallel script or package transport path.
- [x] TDD RED: focused tests failed for the missing Git-native checkout,
  deployment command, CLI, and stale compatibility-label rejection.
- [x] TDD GREEN: focused Git-native tests passed (`11 passed`); the complete
  release-authority test file passed (`32 passed`).
- [x] Pre-commit gates: compile exited 0; the final release-authority suite
  passed (`32 passed`); CLI help smoke, diff, four-path scope, added-lines
  secret, and new-document whitespace checks passed. Self-review found no
  secret/env output, missing public docstring, uncovered primary error path, or
  out-of-scope change. No milestone closes, so guardrails and this Phase record
  replace a changelog or roadmap status change.
- [~] Publication: feature commit
  `c464cbb2e0b41a5313cd6007dc4fafbe4464f334` is pushed and draft PR
  `https://github.com/demonsxxxxxx/ai-platform/pull/417` is open. Initial
  implementation required checks later succeeded; no GitHub review decision
  exists. Final-head literal-safe evidence and controller-dispatched
  independent re-review remain pending.
- [x] Revision 108 gate: generation-2 scope and worktree fingerprints matched
  exact clean head `8ac3a3606e406beb24f47719b2a6e7e381b341aa` before editing.
- [x] Revision 107 review intake: controller-accepted independent task review
  reported zero Critical and two Important findings: ignored worktree files
  bypassed clean-source validation, and two published hyphenated provenance
  labels were absent from compatibility validation.
- [x] Generation-2 RED: five focused tests reproduced Docker lookup before
  ignored-file rejection, unsafe checkout reuse, both label gaps, and missing
  parity mismatches.
- [x] Generation-2 GREEN: the five review regressions passed; after updating
  the Git 1.8 mock contract, the complete release-authority suite passed
  (`37 passed`).
- [x] Generation-2 pre-commit gates: compile and CLI smoke exited 0; the complete
  release-authority suite passed (`37 passed`); diff, exact four-path scope,
  added-lines secret, and Phase whitespace checks passed. Self-review found no
  secret/env output, out-of-scope change, missing primary regression, or
  unsupported closure claim.
- [~] Generation-2 publication: one follow-up commit, push, exact-head PR
  evidence, CI readback, and fresh re-review remain pending.

## Implemented Contract

- `deploy-main-commit` accepts a full 40-hex commit and an absolute normalized
  release root; the env file remains an opaque Compose argument.
- The target checkout explicitly fetches
  `origin main:refs/remotes/origin/main` with Git 1.8-compatible commands,
  verifies the commit object and ancestry, and checks out detached HEAD.
- A commit-named isolated Git checkout is created atomically or reused only
  after origin, HEAD, cleanliness, and fetched-main reachability checks.
- Cleanliness includes ignored untracked files enumerated through Git; such
  files are rejected before image lookup or Docker build. Traversal,
  symlink/junction paths, staging residue, dirty or mismatched releases,
  non-main commits, and invalid commit-shaped input also fail closed.
- Existing `deploy_clean_commit` and `collect_live_parity` remain the build,
  Compose ownership, and runtime parity authorities.
- Worker heartbeat parity derives the fixed heartbeat filename from the
  container's validated absolute POSIX `TMPDIR`, defaults to `/tmp` when the
  entry is absent, and fails closed without exposing environment values.
- Canonical image provenance remains mandatory; every published hyphenated or
  underscore compatibility label, including `ai-platform.source-revision` and
  backend `ai-platform.runtime-subject`, must equal the same commit so inherited
  stale labels cannot pass image reuse or live parity.

## Evidence Boundary

Current evidence ceiling is `PR ready`. This lane does not read a real env,
use local Docker, access or deploy 211, merge, or claim B2, S1B, G0, runtime,
or gate closure. The controller alone owns post-merge 211 rollout and runtime
acceptance. Revision-104 package-transport evidence remains historical current
evidence until that later rollout.
