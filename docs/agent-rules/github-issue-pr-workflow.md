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

## Goal-sized product candidate acceptance

A goal-sized product change must enter review as a draft pull request and pass
pre-merge product acceptance on s72 before it is marked ready or merged. The
acceptance subject is the exact pull-request head commit after its required CI
checks pass. Ordinary focused changes continue to use the local and CI path
above unless their acceptance depends on assembled product behavior.

The s72 subject must use an immutable candidate image identity and an isolated
candidate stack. It must not retag a `main` release image, replace the
latest-main stack, or share its writable database, Redis, workspace, or volume
state. Candidate deployment follows the repository's SSH, read-only readiness,
single mutation-lease, secret-handling, and controlled-host rules. Before the
first candidate deployment, an approved executable candidate procedure must
own image admission, deployment, rollback, and cleanup. Until that procedure
and its image path exist, the gate is `BLOCKED`; source tests, CI image builds,
or an ad hoc source build on s72 are not substitutes.

The pull request records the exact head commit, candidate image digest, named
isolated environment, tested user journeys, and observed result. Any change to
the commit, image, or runtime configuration invalidates that acceptance. This
candidate result does not prove that merged `main` was packaged or deployed;
the normal post-merge release and external-acceptance path remains required.

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

Use precise evidence language and qualify candidate evidence explicitly:

- `local`: named local checks passed on the candidate worktree;
- `CI`: named required jobs passed for the GitHub subject;
- `candidate packaged`: an immutable image was built for the exact pull-request
  head but is not a releasable `main` image;
- `candidate deployed`: that candidate image and isolated configuration were
  applied to the named s72 candidate environment;
- `candidate runtime verified`: that exact candidate subject passed the named
  controlled product checks;
- `packaged`: an immutable release image was built and verified from `main`;
- `deployed`: that release image and configuration were applied to a named
  environment;
- `runtime verified`: the exact deployed release subject passed its controlled
  runtime checks; and
- `external acceptance`: a documented actor completed the named end-to-end
  workflow.

Never promote source, local, CI, candidate, or historical evidence into a
`main` deployment or release-runtime claim. Pre-merge runtime evidence is valid
only as qualified s72 candidate evidence under the goal-sized gate above;
release runtime evidence and External Acceptance still require the post-merge
release procedure.

## Merge and release

Merge only after the applicable required checks and review are complete. Keep
one coherent pull request per independently acceptable change and prefer squash
merge into the protected main branch. A merged source change is not a release:
main packaging, immutable-digest promotion, deployment, runtime verification,
and rollback remain separately observed states.
