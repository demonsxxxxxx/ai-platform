# Authorized Skill Public Selection Projection

Issue: #389

Status: source slice / local partial

## Scope

Expose the selected, materializable Skill version and safe input requirements on
the ordinary-user public Skill list/detail contract. Preserve the existing
tenant, department, role, hidden, disabled, and no-oracle boundaries.

The slice does not change selected-Skill admission, worker/replay behavior,
frontend code, admin APIs, deployment, or 211 runtime state.

## Source Authority

- [x] Start from clean linked worktree at
  `c59c2194bf57718ffb4308cc22da39f3aae46654`.
- [x] Fetch/readback observed `origin/main` advance to
  `c4f24aadfeb96dda4c854f4a2312fb3e3da9957d` through docs-only PR #387.
- [x] Before fixed-SHA review, fetch/readback confirmed `origin/main` at
  `c4f24aadfeb96dda4c854f4a2312fb3e3da9957d`; non-interactive rebase completed
  without conflict.

## Contract

- Public list/detail return `expected_version`, `input_modes`, and
  `requires_file` for already-authorized items only.
- `expected_version` is the selected current/previous rollout version whose
  `content_hash` exactly equals that version.
- Missing or mismatched version/hash rows fail closed.
- `requires_file` is true exactly when `input_modes` contains `docx`, matching
  runs/chat admission.
- Public responses do not expose content hashes, raw grants, rollout controls,
  dependency IDs, admin status, or internal selectors.

## Phase Status

- [x] Phase 0 - base, issue, API evidence, scope, and contract readback.
- [x] Phase 1 - TDD RED: focused run observed 10 expected failures and
  4 passes across missing DTO fields, SQL projection, materializability, and
  previous-rollout hash switching.
- [x] Phase 2 - GREEN: selected content hash and input modes are projected,
  non-materializable rows fail closed, and public list/detail expose only the
  three required fields; focused run passed 14 tests.
- [x] Phase 3 - 305 affected tests passed; compile and diff checks exited 0;
  exact changed-file scope matched the allowlist; added-line secret and personal
  path scan returned no matches.
- [x] Phase 4 - latest-main rebase completed without conflict; independent
  review of `aacb7a578fdc84c9df112e869fe618578cbb191b` found no Critical,
  Important, or Minor issues and independently observed 305 passing tests.
- [~] Phase 5 - ready PR, exact-head evidence comments, and required CI are
  mutable GitHub evidence tracked on issue #389 and its PR; they do not advance
  this source slice beyond local partial or imply frontend/211/runtime closure.

## Evidence

- Baseline: clean branch `codex/389-authorized-skill-public-selection-projection`
  created from exact SHA `c59c2194bf57718ffb4308cc22da39f3aae46654`.
- Current public DTOs omit all three selection fields.
- Current public catalog SQL omits `skills.input_modes` and selected-version
  content hashes; previous rollout switching does not switch a content hash.
- Runtime admission derives required-file behavior from the `docx` input mode.
