# Historical SDK Diagnostic Examples

These examples are historical context, not current merge gates or product
requirements. Apply the generic layered diagnostic rule from
`docs/agent-rules/github-issue-pr-workflow.md` to the current source and runtime.

- PR #165 kept terminal run failures visible instead of hiding them behind
  artifact ACL symptoms.
- PR #168 separated governed worker concurrency from sanitized public
  `sdk_error` diagnostics.
- PR #169 tied controlled runner fallback to observed empty Bash tool-input
  loops while preserving ordinary SDK failure paths.

Do not infer current readiness, acceptance, or required behavior from these PR
numbers without fresh source, issue, and runtime evidence.
