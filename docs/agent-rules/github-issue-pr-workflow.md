# GitHub Pull Request Workflow

Read for delivery or review. Product contracts and release controls stay with
[their owners](../README.md); this guide adds no separate approval system.

## Issue and Pull Request Language

Use Chinese for project discussion, PRs, reviews, and closure records. Preserve
code symbols, commands, fixed template fields, and quoted errors as written.

## Ordinary changes

One coherent PR can hold the complete change record. Use the existing PR
template: describe the outcome, scope, regression coverage, observed checks,
and any remaining limits. A docs-only change reports document verification.
A separate issue, copied SHA inventory, review JSON, or deployment placeholder
is unnecessary. GitHub and CI record the source identity.

For retirement, complete every field in the template's `Retirement /
Compatibility Disposition`: superseded production paths, tests/selectors,
documentation/configuration, retained compatibility surfaces and removal proof,
and a post-change absence or inventory command with its observed result. State
what was removed, which callers and tests moved, and which compatibility
consumers remain. Use `none` only after a current-source inventory proves that
category has no superseded surface. Update the owning documentation in the
same change; do not preserve obsolete code solely for an outdated test.

## High-risk changes

Use a bounded Change Contract in the PR for changes to authentication,
authorization, tenant/workspace isolation, secrets or public redaction,
destructive lifecycle, retention, schema/data compatibility, Sandbox/Tool/Skill/
MCP admission, public API/callback/stream protocols, or CI/release authority.
Record the owner, reached invariants, falsifiable acceptance, evidence limits,
stop conditions, and applicable migration/rollback. An ADR is needed only for
a durable architectural decision or genuine alternative analysis.

Independent GitHub review or a trusted independent check remains required for
these boundaries. Author-written approval claims are not review evidence.
Missing review stays pending. Deferring a high-risk finding requires a human
owner, independent confirmation, and an observable exit condition.

## Local readiness

Run `git diff --check`, relevant static checks, and affected regression tests.
Use the [local test runner](local-test-execution.md) for pytest. Choose order
and breadth by risk; avoid unrelated suites and detached authority checkouts.

When local infrastructure cannot run a required check, record the exact gap
and available results. An authorized push to a draft PR may obtain CI evidence;
it does not permit merging or mark the missing local result as passed. Product
failures remain unresolved until repaired or formally dispositioned.

## Review and findings

Keep findings and disposition in the PR. Prefer a code fix and regression test
to another repository rule. Add a rule only for a repeated defect class with an
owner and a narrow, reliable detector; consolidate overlapping rules.
Do not publish raw transcripts, private prompts, credentials, or sensitive paths.

## Evidence

Use the existing [evidence levels](../architecture/ci-test-readiness-governance.md).
Link the observed result for its actual source. Local, CI, packaged, deployed,
and end-user observations remain distinct; a later fix invalidates earlier
fixed-source verification claims.

## Merge and release

Merge only after applicable required checks and review are complete. Prefer
squash merge for an independently acceptable change. Source merge does not
release the product: deployment and rollback follow the
[release runbook](../operations/release-operations-runbook.md).
