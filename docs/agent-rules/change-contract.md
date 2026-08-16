# Change Contract

## Authority And Purpose

This file defines the pre-implementation contract for repository changes. It
controls how an Agent earns write scope; it does not replace the Issue/PR
record, product architecture, source contracts, review, or runtime evidence.

The current user instruction and accepted repository authority remain higher
authority. If they disagree, continue read-only exploration and repair or
clarify authority before implementation.

## When It Is Required

A Change Contract is required before a change that alters behavior,
authorization, architecture, persistence, a public or durable contract,
configuration, deployment, testing strategy, or more than one owning module.
A local mechanical edit may omit it only when behavior and contract ownership
do not change; record that reason in the task or PR.

Keep the contract in the linked issue, PR, or persistent-task dispatch. Do not
create a repository plan, status page, or phase ledger for an active change.
Create a separate durable design only under the conditions defined by
`github-issue-pr-workflow.md`.

## Required Fields

Every contract records:

1. **Problem** — the current observable failure or missing behavior, stated
   without prescribing the implementation.
2. **Owner** — the single business authority and owning module responsible for
   the decision. Name the transport, adapter, or projection only when it owns a
   separate contract.
3. **Exact subject** — repository/worktree, branch, full 40-hex base SHA,
   current full 40-hex head when it exists, and any runtime subject needed
   later.
4. **Writable paths** — the smallest file or directory set the task may change.
5. **Forbidden paths** — adjacent authorities, generated artifacts, deployment
   subjects, or user-owned changes that the task must not modify.
6. **Behavior delta** — the observable before/after behavior, including failure
   behavior and compatibility decisions.
7. **Invariants** — security, tenant/workspace/user, transaction, lifecycle,
   queue, sandbox, persistence, event, and public-projection facts that remain
   unchanged.
8. **Alternatives considered** — genuine options examined and the concrete
   reason each losing option was rejected. Do not invent alternatives after the
   implementation.
9. **Acceptance and evidence ceiling** — the observable completion conditions
   and the highest evidence level this task is authorized and able to reach.
10. **Regression proof** — the narrowest owning test or purpose-built check that
    would fail if the regression existed. For a bug or guard repair, observe the
    base failure when safe and practical; otherwise state why and use a negative
    invariant test that can actually falsify the candidate.
11. **Assembled and runtime proof** — whether the acceptance claims a real
    route/entrypoint, worker/SDK, browser, packaged image, sandbox, or
    controlled-host behavior and therefore requires that path. A pure
    source-contract change may stop at focused-test evidence when the declared
    ceiling does not claim assembled or runtime behavior. Re-read files or query
    durable state externally instead of trusting an Agent's output about its
    own side effects.
12. **Documentation and decision impact** — owning README/JSDoc/API docs and any
    ADR, architecture document, or Decision Note that must change.
13. **Rollback or recovery** — required for destructive, schema, release,
    deployment, credential, sandbox, and externally visible migrations.
14. **Stop conditions** — facts that revoke write authority: an unknown owner,
    path conflict, base drift, contradictory authority, untestable acceptance,
    required permission, new high-risk scope, or evidence that invalidates the
    selected design.

## Coding Control

- Read the owning implementation, contract, and focused tests before editing.
- Do not edit outside writable paths. Revise the contract first when scope must
  change; a convenient adjacent cleanup is not implicit authority.
- Keep one writer per shared file set. Read-only probes may locate evidence but
  do not decide architecture or acquire write authority.
- Prefer the owning abstraction and current extension point. Do not add a
  compatibility facade, hidden default, public option, or generic helper
  without a named current consumer and a testable requirement.
- Enforce decisions where the operation occurs. UI hiding, request omission,
  prompt wording, and schema shape do not replace an authority check reachable
  by every execution path.
- Publish messages, events, state, and side effects only after the owning
  operation reaches its commit point; test rollback, retry, cancellation, and
  replay where applicable.

## Evidence Rules

Evidence must name the exact subject and the command or observation that
produced it. Use the narrowest credible evidence first:

- focused tests for source behavior and negative contracts;
- real assembled entry paths for model-, user-, CLI-, worker-, and SDK-visible
  behavior;
- build and packaged-artifact smokes for published entrypoints;
- controlled-host runtime checks for sandbox, network, credential, and external
  side effects.

Mocks may replace nondeterministic external boundaries, not the owned behavior
being claimed. A passing test proves only its stated subject. Pending, skipped,
historical, different-SHA, source-only, or self-authored checklist evidence must
retain that weaker label.

## Contract Revision And Completion

Base movement, a material design change, a new owner, or expanded writable
paths invalidates the affected review and verification. Revise the contract,
recompute scope, and rerun only the evidence invalidated by that revision.

The contract is satisfied only when its acceptance items are observed or
explicitly left at the declared evidence ceiling. Closure and status language
remain governed by `github-issue-pr-workflow.md`.
