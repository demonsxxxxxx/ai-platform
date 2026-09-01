# GitHub Pull Request Workflow

This file owns pull-request scope, review, verification, and evidence language.
Product and deployment invariants remain in their architecture documents and the
release runbook.

## Ordinary changes

A focused ordinary change may use its pull request as the complete durable
record. It does not require a separate issue, hand-copied Git SHA, worktree
inventory, structured review JSON, deployment placeholder, or runtime evidence.

The pull request states:

- the problem and intended outcome;
- changed behavior, owning modules, and explicit non-goals;
- a falsifiable regression test and observed focused checks;
- checks not run and why; and
- only the risk boundaries the change actually reaches.

The actual GitHub event supplies the base and head identities. CI and release
workflows bind evidence to those identities automatically; authors do not copy
SHAs into pull-request text.

## High-risk changes

Use a bounded Change Contract for goal-sized work or changes that reach any of
these boundaries:

- authentication, authorization, tenant or workspace isolation;
- secrets, credentials, or ordinary-user projection redaction;
- destructive lifecycle, retention, schema migration, or irreversible data
  compatibility;
- sandbox, command, tool, Skill, MCP, or executor admission;
- public API, callback, event, or streaming protocols; or
- workflow, image, release, deployment, or rollback authority.

The contract records the owner, bounded paths, reached invariants, acceptance,
falsifiable regression proof, evidence ceiling, rollback or migration plan when
relevant, and stop conditions. A separate ADR or design is required only when a
durable architecture decision or genuine alternative analysis is needed.

High-risk review uses real GitHub review or an independently produced trusted
check. Pull-request text written by the author is not proof that another reviewer
acted. Until an independent reviewer is available, do not claim formal approval;
retain the risk-specific tests and owner authorization without inventing a
review identity.

## Local readiness

Before pushing, run the smallest checks that can falsify the change from the
candidate worktree:

1. `git diff --check`;
2. relevant compile, formatter, lint, type, schema, or generated-code checks;
3. the owning regression test; and
4. bounded compatibility or integration tests justified by the changed risk.

Use `tools/run_test_stage.py` for ordinary local pytest execution. Local checks
are developer feedback, not trusted merge authority. GitHub required checks run
the accepted trusted-base governance and exact candidate tests; authors do not
create detached authority worktrees before every push.

## Review and findings

Review comments are the disposition record for ordinary changes. Resolve fixed
findings in the pull request. A deferred or rejected high-risk finding requires
an identifiable human owner, independent confirmation, and a falsifiable exit
condition. Do not paste raw review transcripts, prompts, credentials, private
local paths, or secret-bearing payloads into GitHub.

Promote a lesson only to its smallest durable owner:

- a one-off defect becomes a code fix;
- a reproducible regression becomes an owning test; and
- a repeated high-cost defect class may become a repository rule only when its
  detector is deterministic, narrowly scoped, owned, and demonstrably low in
  false positives.

A new rule replaces or consolidates overlapping policy. Review wording and
per-change JSON are not durable architecture artifacts.

## Evidence levels

Use precise evidence language:

- `local`: named local checks passed on the candidate worktree;
- `CI`: named required jobs passed for the GitHub subject;
- `packaged`: an immutable image was built and verified;
- `deployed`: that image and configuration were applied to a named environment;
- `runtime verified`: the exact deployed subject passed its controlled runtime
  checks; and
- `external acceptance`: a documented actor completed the named end-to-end
  workflow.

Never promote source, local, CI, or historical evidence into a production
runtime claim. Runtime evidence normally begins after merge under the release
and controlled-host procedures. A separately governed isolated pre-merge
candidate may produce candidate-runtime evidence only when a stable
accepted-base required context binds the exact PR base/head, immutable digests,
configuration, trusted delivery run, and controlled-host evidence. That result
never makes the candidate release eligible and never substitutes for post-merge
production External Acceptance.

## Merge and release

Merge only after the applicable required checks and review are complete. Keep
one coherent pull request per independently acceptable change and prefer squash
merge into the protected main branch. A merged source change is not a release:
main packaging, immutable-digest promotion, deployment, runtime verification,
and rollback remain separately observed states.
