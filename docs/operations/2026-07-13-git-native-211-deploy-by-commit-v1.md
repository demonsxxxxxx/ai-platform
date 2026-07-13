# Git-Native 211 Deployment By Commit V1 Phase Status

Lane: `git-native-211-deploy-by-commit-v1`, generation 1, phase 1.

Branch: `codex/git-native-211-deploy-by-commit-v1`.

Base: `678d3c46f7b7dc41cf5d99d5b7898551f1bb50f3`.

Issue: `https://github.com/demonsxxxxxx/ai-platform/issues/415`.

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
- [ ] Publication: scoped commit, push, draft PR, literal-safe validation
  evidence, CI readback, and controller review dispatch remain pending.

## Implemented Contract

- `deploy-main-commit` accepts a full 40-hex commit and an absolute normalized
  release root; the env file remains an opaque Compose argument.
- The target checkout explicitly fetches
  `origin main:refs/remotes/origin/main` with Git 1.8-compatible commands,
  verifies the commit object and ancestry, and checks out detached HEAD.
- A commit-named isolated Git checkout is created atomically or reused only
  after origin, HEAD, cleanliness, and fetched-main reachability checks.
- Traversal, symlink/junction paths, staging residue, dirty or mismatched
  releases, non-main commits, and invalid commit-shaped input fail closed.
- Existing `deploy_clean_commit` and `collect_live_parity` remain the build,
  Compose ownership, and runtime parity authorities.
- Canonical image provenance remains mandatory; present underscore
  compatibility aliases must equal the same commit so inherited stale labels
  cannot pass image reuse or live parity.

## Evidence Boundary

Current evidence ceiling is `PR ready`. This lane does not read a real env,
use local Docker, access or deploy 211, merge, or claim B2, S1B, G0, runtime,
or gate closure. The controller alone owns post-merge 211 rollout and runtime
acceptance. Revision-104 package-transport evidence remains historical current
evidence until that later rollout.
