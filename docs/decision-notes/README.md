# Decision Notes

## Purpose And Boundary

A Decision Note preserves a focused engineering choice whose rationale is
likely to outlive its issue or PR. It records why one option won and what was
given up. It is not a task plan, implementation journal, runtime-evidence log,
or substitute for source contracts and tests.

Use an existing owning document instead of creating a duplicate. Security,
authorization, tenant isolation, persistence, concurrency, public contracts,
release, deployment, runtime, and infrastructure decisions that establish
durable platform authority belong in an ADR or indexed architecture document
under `docs/README.md`, not in a lightweight Decision Note.

## When To Write Or Update One

Write or update a Decision Note when a non-obvious, reusable choice would
otherwise be lost after the issue or PR closes, especially when a future Agent
could plausibly reintroduce a rejected design. A local mechanical change, an
obvious bug fix, temporary rollout state, or information already owned by an
ADR/architecture document does not need one.

Store notes as `docs/decision-notes/YYYY-MM-DD-topic.md`. Link the owning issue,
PR, code, tests, and higher authority rather than copying their contents.
Update the existing owner for the same decision; do not create competing notes.

## Required Format

```markdown
# Decision Note: <title>

Status: proposed | implemented | rejected | superseded
Owner: <business authority/module>
Decision issue: #<number>

## Problem

<Observable problem independent of the preferred solution.>

## Proposal

<For proposed notes only. Describe the intended decision and boundaries.>

## Decision

<For implemented notes. Describe current shipped reality in present tense.>

## Alternatives considered

**<Alternative>.** Rejected because <specific reason or trade-off>.

## Verification and evidence boundary

<Owning tests, assembled checks, and evidence levels not yet observed.>

## Consequences and revisit triggers

<What the choice buys, costs, and the facts that justify reconsidering it.>

## Related authority

<Links to issue/PR, code/tests, ADR or architecture owner.>
```

`Alternatives considered` is mandatory and records genuine alternatives, not
retroactive justification. Use `Proposal` only for a proposed or rejected note
and `Decision` only for an implemented or superseded note; do not keep both
sections in one completed note. A rejected note keeps the proposal and puts the
reason in its status or Proposal section. A superseded note links its successor
and is not current authority.

## Lifecycle Rules

- Proposed text may describe future work; implemented text describes only
  shipped current reality.
- Changing a file path, owner name, or current verification reference may
  update an implemented note. Reversing the decision requires a new note or a
  higher-authority ADR and an explicit supersession link.
- Runtime observations remain in reviewed release evidence and are historical
  unless freshly verified. A note may link them but must not promote them.
- Delete notes that merely repeat a closed issue and no longer prevent a
  plausible mistake. Git history is sufficient for low-value implementation
  narration.
