---
name: ai-platform-review
description: Use when reviewing an ai-platform pull request, branch, or diff, including self-review before marking a PR ready. Orients the reviewer to the owning contracts, platform boundaries, evidence rules, and the semantic checks that CI cannot show.
---

# Reviewing an ai-platform change

This skill is guidance, not a complete checklist. Prioritize correctness,
boundary, lifecycle, and broken required behavior over style. One substantiated
blocker is worth more than a list of nits. Write findings in Chinese, per the
[delivery workflow](../../../docs/agent-rules/github-issue-pr-workflow.md).

## Independence

Review from a fresh context. The session that wrote the change may self-review
before requesting review, but its conclusion is not review evidence. Treat the
PR body, commit messages, and agent reports as claims to check, not as proof:
rerun or inspect each verification and inventory command they cite.

## Establish the exact change

1. Fetch and verify the PR's actual target branch and exact base/head SHAs.
   For a stacked PR, use its target phase branch. For a local branch or diff,
   establish the intended integration target before choosing the base.
2. Inspect the full scope before reading hunks:

```sh
git fetch origin
git diff --stat <base>...<head>
git log --oneline <base>..<head>
```

For architecture checks, distinguish the comparison base from the trusted
authority selected by the accepted governance workflow. The checker requires
authority to be an ancestor of base, and base to be an ancestor of head. Report
an invalid range and follow the owning workflow; do not substitute older refs
just to make the check pass.

Re-establish the base and rescan after a retarget, rebase, or target-branch merge.
Flag a PR that mixes unrelated concerns or is too large to review as a whole;
ask for a split instead of approving parts you did not read.

### Migration-triggered consumer checks

Choose these checks only when the changed subject reaches the named surface; do
not turn them into a full-repository audit:

- deleting or moving `tests/test_*.py`: inspect `.github/workflows/` shards,
  package scripts, and literal lists in `tools/` or `tests/`; use the existing
  workflow-owner contract
  (`test_every_backend_test_file_has_one_complete_ci_owner`) for the affected
  workflow scope;
- changing a shared interface or injection point: search unchanged fakes,
  mocks, fixtures, `monkeypatch` targets, and support adapters, then run or
  inspect valid candidate-specific evidence for the owning selector reached by
  `tools/git_hook_checks.py`/the local runner. If execution is unavailable,
  hand the concrete command to the parent; missing mandatory evidence remains
  blocked;
- changing event publication or registration: inspect the existing publication
  inventory and its owning check, such as
  `tools/check_sse_runtime_cutover.py` and `tests/test_sse_runtime_cutover.py`;
- changing a check command: trace its workflow, script, and traceability
  consumers and run or inspect valid candidate-specific evidence for the
  existing contract test for that path.

Record the short consumer search and the exact owning checks used. If a named
mandatory surface is not inspected, mark the review `INCOMPLETE`/`BLOCK` rather
than treating a local partial review as a whole-PR conclusion. The local hook's
affected-test selection is feedback, not proof of a GitHub push, CI, or
end-to-end result. Reuse valid risk-selected evidence and do not rerun unaffected
suites merely for completeness.

## Read the requirements before judging the diff

Map changed paths to owners, open each owning page, and only then read the
diff against it. Judging a diff against memory of the rules is how violations
pass review. Load only the owners the diff reaches.

- [AGENTS.md](../../../AGENTS.md): placement, verification, boundaries, retirement.
- [Documentation authority](../../../docs/README.md): the single detailed owner
  for each runtime, protocol, data, and frontend concern.
- [Source architecture](../../../docs/architecture/source-code-architecture.md)
  and `architecture-policy.json`: bounded contexts, layers, public `api`/`events`.
- [Evidence governance](../../../docs/architecture/ci-test-readiness-governance.md):
  what a local, CI, packaged, or deployed result actually proves.
- [Web frontend instructions](../../../frontend/web/AGENTS.md) for `frontend/web/`.
- Accepted ADRs under `docs/adr/` when the diff touches a recorded decision.

Establish the observable behavior being changed or preserved. Trace the affected
entrypoint through authority checks and durable writes to its direct consumers,
including unchanged callers. For Run changes, follow the affected links in
admission → dispatch → execution → settlement → delivery → cleanup → next turn.
Use the owning tests and contracts to seek a concrete input, state, or failure
that contradicts the intended result; choose depth by the change's risk.

## Blocking requirements

1. **Boundaries hold.** Tenant, workspace, user, Run/Attempt, and lease
   boundaries are preserved. Admission, authorization, context binding,
   persistence, and public projections stay platform-owned; SDK types stay in
   the adapter.
2. **Public projections stay clean.** Ordinary-user responses, SSE events, and
   UI never expose raw Skill identifiers, storage keys, runtime paths, command
   fingerprints, private executor payloads, secrets, or private prompts. Check
   error and diagnostic paths, not only the success path.
3. **Placement follows ownership.** New code lives in the owning context's role
   layer; cross-context access goes through `api`/`events`. No new generic
   `services`, `utils`, or `tools` buckets, and no re-exported infrastructure
   adapters to make an import check pass.
4. **Retirement is complete.** For delete/replace/migrate changes, every field
   of the PR's retirement disposition is filled, and you reran or inspected its
   absence/inventory command. No old implementation, stale assertion, selector,
   or instruction stays active without an owner and removal proof.
5. **High-risk changes carry a Change Contract.** Authentication,
   authorization, isolation, secrets/redaction, destructive lifecycle,
   retention, schema/data compatibility, Sandbox/Tool/Skill/MCP admission,
   public API/callback/stream protocols, and CI/release authority need the
   contract and independent review. Missing review stays pending.
6. **Tests prove the change.** Each behavior change has an owning regression
   test that would fail on the intended regression. Required checks are not
   weakened, skipped, or narrowed to make the candidate pass.
7. **Docs match the code.** Owning contracts change in the same diff as the
   behavior, config, wire field, or default they describe.

## Manual checks

- **Model perspective:** for prompt, tool schema, permission, or SDK-runner
  changes, read what the model actually receives across affected modes. Flag
  concepts outside the model's task and verify stable text verbatim.
- **Lifecycle and concurrency:** check lease fencing, cancellation during
  awaits, idempotency and replay, terminalization ordering, retries, and
  cleanup on every exit path. For affected races, interleave owners around
  awaits and commits: can an old owner write after takeover, or a crash after
  commit lose the next step or repeat a side effect? Check the actual write.
- **Migration:** inspect the supported old/new reader and writer combinations,
  persisted data, cutover order, and rollback or forward recovery. Challenge
  compatibility after a new writer has committed facts, using the owning
  contract's supported deployment states.
- **Enforcement:** follow each denial to the operation that executes it, and
  try alternate callers that bypass the route, facade, prompt, or frontend.
- **Bounds:** limits cover the final emitted or stored result; probe exact and
  oversized sizes and multibyte text where byte limits apply.
- **Real entry path:** tests exercise the shipped route, worker, callback, or
  sandbox entry, not a hand-assembled internal object that skips admission.
- **Negative controls:** each new acceptance rule has a deliberately invalid
  case that fails through the real runner.
- **Error handling:** reject catch-and-continue unless the state owner proves
  later work's preconditions still hold; user-visible errors stay public-safe.
- **Necessity:** challenge speculative options, compatibility paths without an
  identified consumer, and new public methods with a single internal caller.
- **Size:** flag growth of files the source-quality inventory already marks as
  hotspots; prefer extraction along an ownership boundary over appending.
- **Evidence levels:** local, CI, packaged, deployed, and end-user results stay
  distinct. Later changes invalidate affected conclusions; retain the original
  source and scope when reusing unaffected evidence. Select rechecks using
  [change verification](../../../docs/agent-rules/change-verification.md).

## Reporting findings

For each finding state the trigger, expected and actual behavior, location,
impact, and evidence. Distinguish verified facts from unverified hypotheses. Place
localized defects inline on the tightest diff range; use a PR-level comment for
cross-cutting scope or architecture. Separate blockers from suggestions and
omit issues a green required check already enforces. Keep findings and their
disposition in the PR. When receiving review, verify each claim and fix or
rebut it on technical grounds.
