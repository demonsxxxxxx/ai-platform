# Historical SDK Diagnostic Examples

These examples are historical context, not current merge gates or product
requirements. Use the current [delivery workflow](../github-issue-pr-workflow.md)
for PR handling and evidence boundaries; these examples add no diagnostic rule.

- PR #165 kept terminal run failures visible instead of hiding them behind
  artifact ACL symptoms.
- PR #168 separated governed worker concurrency from sanitized public
  `sdk_error` diagnostics.
- PR #169 tied controlled runner fallback to observed empty Bash tool-input
  loops while preserving ordinary SDK failure paths.

Do not infer current readiness, acceptance, or required behavior from these PR
numbers without fresh source, issue, and runtime evidence.
