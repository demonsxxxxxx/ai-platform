# Working in ai-platform

Repository coding instructions. Product `Agent.md` files describe Agent
Profiles or workspaces; they do not govern repository changes.

## Work from the current source

Use the current worktree and preserve unrelated edits. Choose implementation,
planning, delegation, and test scope to fit the task. Routine changes need no
separate issue, phase ledger, review transcript, or worktree.

Read the affected code and its owning contract. Use the links below when the
task reaches that concern; do not recursively load every linked document.
Historical evidence and `.codegraph` are navigation aids, not current runtime
proof. Keep progress and blockers in the active task or PR.

## Verification and cleanup

Cover changed behavior with an owning regression test; reuse existing coverage
when sufficient. Use `python tools/run_test_stage.py` for local pytest. Choose
additional checks by affected risk, not a fixed sequence or whole-repo ritual.
Documentation-only edits need relevant document checks, not invented runtime
proof. Report checks run, results, and limits; never present a patch as pushed,
a build as deployed, or an unobserved test or review as passed.

Finish a migration by updating callers, tests, selectors, and owning docs and
removing the replaced path. Keep compatibility only for identified consumers.
Do not weaken required checks to make a candidate pass.

## Boundaries to preserve

- Platform admission, authorization, context binding, persistence, and public
  projections remain platform-owned; SDK-specific types stay in the adapter.
- Preserve tenant, workspace, user, Run/Attempt, and lease boundaries.
- Ordinary-user projections exclude raw Skill identifiers, storage keys,
  runtime paths, command fingerprints, private executor payloads, and secrets.
- Never copy, print, or commit real environment files, credentials, account
  identifiers, or private prompts. Use synthetic or authorized redacted data.
- Fake providers are test-only. Do not mount the Docker socket in default Compose.
- Access s72 only through SSH MCP, with one bounded, secret-safe connection check
  when remote work is requested. Do not substitute system SSH or local state.
  Deployment requires the release runbook, fresh evidence, and explicit authority.

Every behavior change must carry a retirement and compatibility disposition in
its PR before review. Inventory superseded production paths, tests/selectors,
and documentation/configuration; remove each obsolete surface or name the
current compatibility owner and its removal proof. A behavior change is not
complete while an old implementation, stale assertion, selector, or
instruction remains active without an explicit disposition and an
absence/inventory check. The checklist is a review index, not evidence by
itself; reviewers must inspect or rerun the referenced inventory.

## Read when relevant

- Code ownership and product contracts: [documentation index](docs/README.md).
- Python checks: [local test execution](docs/agent-rules/local-test-execution.md).
- PR and review: [delivery workflow](docs/agent-rules/github-issue-pr-workflow.md).
- Parallel work: [coordination](docs/agent-rules/multi-agent-context-workflow.md).
- Deployment: [release runbook](docs/operations/release-operations-runbook.md).
