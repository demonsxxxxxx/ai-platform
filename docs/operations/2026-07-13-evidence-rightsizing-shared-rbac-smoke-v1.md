# Evidence Right-Sizing and Shared RBAC Smoke V1

Status: `PR ready`

## Phase Status

- [x] Phase 0: controller revision-105 envelope and exact base/head
  `678d3c46f7b7dc41cf5d99d5b7898551f1bb50f3` were accepted. The independent
  scope fingerprint matched `sha256:3a198276900a8e8384555870e9ffd726798320f5e407b0f6c67bf67dd216c71c`;
  the clean worktree fingerprint matched
  `sha256:569d756187855990e72143b513e78c89b92e28044bc89ddf72cdedbc05d23ac8`.
- [x] Phase 1: tracking issue [#416](https://github.com/demonsxxxxxx/ai-platform/issues/416)
  records the ordinary-slice plan and status. This document is the only concise
  Phase record for the slice; it is not a separate spec/plan/status trio.
- [x] Phase 2: source-contract RED observed because the shared harness did not
  exist; the unchanged company RBAC assertion contract passed in that RED run.
- [x] Phase 3: scenario-neutral Chrome/CDP lifecycle, timeout, screenshot,
  redacted child diagnostics, and isolated-profile cleanup moved to the shared
  harness. The company scenario retains mock identities and RBAC assertions.
- [x] Phase 4: direct backend permission tests passed `3/3`; the affected
  permission-gated route test passed `1/1`; frontend policy/source tests passed
  `8/8`; `python -m compileall -q app tools scripts`, script syntax checks, and
  `tsc -b` exited `0`. ESLint exited `0` with 13 pre-existing warnings. The
  isolated mock-backed smoke passed all four ordinary/admin desktop/mobile cases;
  it recorded four browser-ready, exit, and profile-cleaned events with no
  cleanup failure or newly retained temporary profile.
- [x] Phase 5: draft PR [#418](https://github.com/demonsxxxxxx/ai-platform/pull/418)
  publishes the literal-safe command and result summary. It is `PR ready` for
  review only; no independent review, merge, deployment, 211 verification, or
  gate closure is claimed.
- [x] Phase 6: revision-107 fixed four review findings after four independent
  source-contract RED failures: child exit now escalates and fails closed before
  profile removal; failure and lifecycle output use the shared redaction value
  path; `ci:verify` invokes the company source contract; and evidence sizing
  explicitly requires separate design for security, auth, tenant isolation,
  release/deployment, runtime, and other high-risk changes. Focused backend
  tests passed `4/4`, frontend policy/source tests passed `12/12`, compile,
  lint (0 errors; 13 existing warnings), TypeScript, and `ci:verify` passed.
  The isolated mock smoke again passed four cases with no new profile retained.
- [x] Phase 7: revision-109 addressed the exact packaged-image CI RED from
  Actions run `29261978319`: the frontend source contract no longer reads
  repo-root governance documents, while backend required now enforces the same
  bounded separate-design trigger assertion. Frontend source tests passed `7/7`;
  backend CI workflow tests passed `3/3`; compile, lint (0 errors; 13 existing
  warnings), TypeScript, diff, and sensitive-marker checks passed. No browser,
  Docker, 211, or product behavior was used or changed in this phase.
- [x] Phase 8: revision-112 preserved the two stale traceability-contract RED
  failures and added RED coverage for the missing `pyyaml` install and combined
  PowerShell command shape. The workflow now installs `pytest pyyaml` and runs
  the selected Python suite before the deploy-helper help command in separate,
  single-native-command steps. The traceability contract verifies both the full
  `ci:verify` value and those exact steps; the focused local contracts passed
  `21/21`. Fresh GitHub Actions logs remain required after the follow-up push
  before claiming the CI repair is ready for re-review.
- [x] Phase 9: revision-113 moved the unchanged governance-readiness CLI test
  from the dependency-light frontend selected suite to backend required, which
  already installs the project dependency set. Source contracts require that
  ownership, prohibit frontend duplication, and retain the revision-112
  `pytest pyyaml` install plus split pytest/helper steps. Focused local
  contracts, including governance readiness, passed `28/28`. Fresh frontend
  and backend Actions logs remain required after the follow-up push.

## Evidence Boundary

No real credentials, existing browser state, 211 access, deployment, CI workflow,
product behavior, historical evidence, or the other three browser smoke scripts
are in scope. Local results can establish at most `PR ready`, never deployment,
211 verification, merge, or gate closure.

## Follow-up Migration Order

1. MCP admin smoke, after a separate scope decision because it owns Vite and
   richer diagnostics.
2. Authorized-Skill smoke, after its attachment and stale-state requirements are
   independently reviewed.
3. PRD closure smoke, after its real-login and provenance requirements are
   separately scoped.
