# Issue #475: Zero-click runtime tool policy

## Scope and decision

`app.tool_policy.evaluate_tool_policy` is the sole runtime tool-policy seam.  It
returns an `allow` or `deny` outcome plus a stable reason and audit metadata;
it never returns an approval state, decision reference, or wait instruction.

An adapter supplies the requested tool identity and the already-authorized run
subject.  The policy canonicalizes only exact built-in names and exact
`mcp__SERVER__TOOL` names, then allows only a registered, declared, active and
distributed capability.  Invalid identity, missing declaration, disabled or
undistributed capability, and failed identity/object/parameter checks deny
synchronously.  Risk and write metadata are retained for audit only.

Bare-name compatibility is an adapter concern: an adapter may map a required
legacy bare spelling to one canonical declaration before calling the policy.
The policy never broadens a prefix, case variant, malformed value, or external
MCP name.

## Changes

1. Replace the ask/grant policy and worker grant consumption with the one
   allow/deny seam; retain existing distribution and service/object checks.
2. Remove SDK callback transport, request-row production, decision lookup,
   polling, grant consumption and replay.  Sandbox and legacy callback adapters
   fail closed without reaching the resolver.
3. Make runtime request/decision routes unavailable (fail closed); retain
   authorized historical reads, redaction, audit history, and terminalization
   of pre-existing rows.  No schema migration is included.
4. Remove the administrator model-tool Inbox, both mounts and its decision
   client.  Role-governance approval is outside this change.
5. Update readiness/projection wording and focused backend/frontend tests to
   assert zero-click execution and no pending permission state.

## Invariants and compatibility

- Tenant, user, workspace, session, run, file and artifact authorization is
  not delegated to the tool-policy result.
- Sandbox filesystem, resource, egress and credential controls remain in
  force; policy allow is not an execution-boundary bypass.
- Existing `run_tool_permission_requests` rows remain readable/redacted and
  can terminalize safely.  Historical events remain parseable, but new runtime
  events contain no pending request, decision endpoint, or request id.
- The removed endpoints and callback fail closed before any mutation.

## Verification plan

- RED/GREEN unit tests for canonical identity, declared high/write allowance,
  all deny classes, routes, callback isolation, worker policy audits and no
  producer source invariant.
- Affected Python tests plus frontend source tests with a fresh workspace-local
  pytest base temp, then `python -m compileall -q app tools scripts`, frontend
  static tests where available, `git diff --check`, and a focused self-review.
