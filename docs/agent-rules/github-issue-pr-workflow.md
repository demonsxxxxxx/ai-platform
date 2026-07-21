# GitHub Issue And PR Workflow

This workflow applies to goal-sized ai-platform work, gate closures, and newly
discovered defects. Keep `AGENTS.md` as the short entry point and update this
file when the issue/PR workflow needs more detail.

## Closure Loop

Use this sequence by default:

1. Create or update issue.
2. Implement on a branch.
3. Open PR.
4. Run local verification.
5. Request and resolve review.
6. Merge.
7. Deploy and smoke on 211 when required.
8. Close the issue with evidence.

## CI/CD And Evidence Strategy

Only checks from concrete GitHub workflows applicable to the changed path and
actually observed on the PR count as CI merge gates. Do not invent an implicit
CI status, and do not wait for a nonexistent or inapplicable run.

Use durable GitHub evidence alongside applicable CI:

- Run the focused local verification required for the changed path.
- Run independent review when the issue, gate, or risk level requires it.
- Post the review result and verification commands to the PR or issue before
  merge or issue closure.
- Treat chat-only review, local-only sub-agent output, or unposted terminal
  output as insufficient for `reviewed`, `merged`, or `gate closable` claims.

If the missing CI/CD coverage itself becomes a blocker, create a separate issue
and PR for the CI/CD workflow. Do not silently upgrade every unrelated PR into a
CI/CD implementation task.

## Evidence Sizing

For an ordinary implementation slice, use the linked GitHub issue and PR as the
plan, change description, and continuing status record. Do not require a
separate spec/plan/status trio by default.

Create a separate design only when the slice changes a schema or public API,
persistence, concurrency, infrastructure, or leaves an unresolved cross-module
decision. A medium or long task may maintain one concise Phase status document
when it improves handoff or verification clarity.

Create a separate design for security, auth or authorization, tenant isolation,
release or deployment, runtime, and other high-risk changes even when the slice
is otherwise small.

This sizing rule does not weaken verification. Keep risk-proportionate machine
evidence: exact authorization and route checks, policy tests, and the relevant
local or deployed smoke remain required. Do not delete or rewrite historical
evidence merely because a future ordinary slice uses the smaller record.

## Status Language

Use precise status labels. Do not let an earlier status imply a later one.

- `local partial`: targeted tests or one focused smoke passed.
- `PR ready`: code, focused tests, and docs are ready for review, but the PR has
  not merged and runtime evidence may still be missing.
- `reviewed`: independent review has run, and every finding is fixed, rejected
  with evidence, or tracked as a follow-up issue.
- `211 verified`: deployed runtime has been checked on 211 with the correct
  container, image, route, principal, and contract behavior.
- `gate closable`: issue, PR, review, tests, docs or roadmap updates, and
  required 211 evidence are all complete.

Never describe `local partial`, `PR ready`, or `reviewed` work as deployed,
closed, or gate-complete.

## Issue Triage

Before implementation, read the relevant PRD, roadmap, guardrails, current code,
fresh 211 state, and the existing GitHub issue.

If the problem is new, create or update a GitHub issue with:

- scope;
- affected gate or roadmap section;
- acceptance criteria;
- local verification requirement;
- review requirement;
- 211 deployment or smoke requirement when relevant;
- known blockers or missing evidence.

Do not hide blockers in chat. If review, tests, 211 smoke, source authority,
auth, tenant quota, sandbox, or frontend projection evidence is missing, record
it on the issue or PR before pausing.

## Branch And PR Rules

- Keep one coherent PR per issue or gate slice.
- Use a branch name that includes the issue number or gate name.
- A PR may close multiple issues only when their acceptance criteria are truly
  covered by the same slice.
- Direct commits to `main` are exceptions. Use them only when the user
  explicitly requests direct-main work or when operational recovery requires it.
  Still record the issue/PR exception and final evidence.

## PR Description Requirements

Every PR should state:

- linked issue or gate;
- changed modules;
- user-visible or operator-visible behavior;
- tests run;
- review status;
- docs or roadmap updates;
- 211 deployment or smoke evidence, or why it is not required.

Use `Closes #N` or `Fixes #N` only when the issue acceptance criteria are
covered by code, tests, docs or roadmap updates, review, and required 211 smoke.
Otherwise link the issue without auto-closing language.

## Review And Verification

- High-risk or stage-gate work requires independent review when the available
  delegation path is suitable.
- SDK / worker diagnostics must be layered when a PR changes Claude Agent SDK,
  skill execution, worker launch, terminal execution, or user-facing runtime
  errors. Trace the fault through:
  `tool registration -> runner selection -> subprocess/terminal -> SDK event -> user-facing error`.
  Each such PR must leave at least one minimal reproduction and one observable
  log or event evidence item on the PR or linked issue. Generic `sdk_error`,
  empty Bash input loops, terminal run failures, missing native skill evidence,
  and platform-controlled runner selection must be classified at the layer where
  they are observed. Local diagnostic evidence by itself is `local partial` or
  `PR ready` evidence only; it is not `reviewed`, `211 verified`, or
  `gate closable`, and it carries no #164 or stage/gate closure claim
  unless the normal review, deployed-runtime, and issue-closure gates below also
  pass.
  Historical examples are non-normative and live in
  `docs/agent-rules/history/github-sdk-diagnostic-examples.md`.
- When review is performed by sub-agents or other local assistants, record the
  result on GitHub before using it as review evidence. The comment should name
  the reviewer role, scope, findings, fixes or rejections, and verification
  evidence.
- When the repository does not have an available GitHub review robot or formal
  GitHub reviewer, a sub-agent review recorded on the PR is the accepted
  independent review substitute. Before merge, run a follow-up sub-agent
  re-review after fixes and verify that no Critical or Important findings remain
  unhandled.
- If GitHub `reviewDecision` is empty, do not call the PR formally approved.
  Label the state explicitly, such as `sub-agent review substitute`,
  `user-authorized review substitute`, or `inherited-configuration review`, and
  keep that separate from a GitHub formal review decision.
- Validate review findings against current PRD, roadmap, guardrails, code,
  tests, and 211 evidence before changing code.
- Every review finding must be handled in one of three ways: fixed with tests,
  rejected with a written evidence-backed reason, or moved to a follow-up issue.
  Do not leave review findings only in chat.
- Before PR, deployment, merge, or stage-gate closure, run targeted tests for
  the changed or affected modules plus relevant integration or smoke checks
  unless the task is explicitly documented as no-code. Do not require or run
  full-repository pytest as a routine gate.
- For public/admin projection changes, verify the correct principal and route.
  Admin checks do not prove ordinary-user behavior.
- For 211 deployment, prove current deployed containers, image identity, API
  health, and the relevant contract behavior after deployment.
- If deployment needs `docker cp`, runtime-only rebase, `--no-build`, or another
  workaround, label it as a workaround in the PR or issue. If the same workaround
  is needed repeatedly, open a follow-up issue for the release path.

## Shortcut Prevention Checklist

Before saying `complete`, `closed`, `deployed`, `passed`, or `ready to merge`,
confirm:

- there is an issue or explicit gate;
- a PR exists, or a direct-main exception is recorded;
- the correct local test level ran;
- review ran when required, and findings were fixed, rejected with evidence, or
  tracked as follow-up issues;
- 211 smoke ran when required, with the correct principal, route, container, and
  image evidence;
- docs or roadmap updates are present when the slice changes gate status;
- deployment workarounds are labeled as workarounds;
- the issue can be closed without relying on chat-only evidence.

## Issue Closure

Close after evidence, not after intent.

An issue is closed only after:

- the linked PR has merged, or a no-code decision is recorded;
- required local verification is posted;
- required review is posted;
- required docs or roadmap updates are present;
- required 211 deployment or smoke evidence is posted.

For no-code issues, close with a comment that cites the verification or decision
evidence. Do not use auto-close wording on a PR if any issue acceptance item is
still pending.
