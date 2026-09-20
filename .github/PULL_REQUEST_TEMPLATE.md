## Purpose

- Problem and intended outcome:

## Scope

- Changed behavior and owning modules:
- Explicit non-goals:

## Retirement / Compatibility Disposition

For behavior changes that delete, replace, migrate, or alter compatibility,
complete every field in the template's `Retirement / Compatibility Disposition`:
superseded production paths, tests/selectors, documentation/configuration,
retained compatibility surfaces and removal proof, and a post-change absence or
inventory command with its observed result. For other behavior changes, state
that no superseded surface is in scope. State what was removed, which callers and
tests moved, and which compatibility consumers remain. Use `none` only after a
current-source inventory proves that category has no superseded surface. Update
the owning documentation in the same change; do not preserve obsolete code
solely for an outdated test.

- Superseded production paths:
- Superseded tests and selectors:
- Superseded documentation and configuration:
- Retained compatibility surfaces and removal proof:
- Post-change absence or inventory check (command and result):

## Verification

- Falsifiable regression test:
- Commands run and observed results:
- Checks not run and why:

## Risk

Name only the risk boundaries defined in
`docs/agent-rules/github-issue-pr-workflow.md` that this change reaches.

- Reached boundaries and preserved invariants:

## High-risk changes only

- Design or Change Contract:
- Independent review and rollback or migration plan:
