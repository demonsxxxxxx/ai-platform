## Subject and scope

- Issue / Change Contract:
- Repository/worktree and branch:
- Full base SHA / candidate head SHA:
- Writable paths / forbidden paths / non-goals:
- Actual diff reconciled with scope:

## Behavior and decision

- Observable problem and owning authority:
- Before/after, failure, and compatibility behavior:
- Applicable invariants and explicitly non-applicable risk categories:
- Alternatives considered and why they lost:
- Acceptance criteria and declared stop conditions:
- Documentation or separate-design impact:

## Evidence and recovery

- `N/A` is reserved for the risk categories above. For behavior, tests,
  evidence, review, and rollback, give observed facts or explain why the field
  does not apply; a bare `N/A` is not an answer.
- Falsifiable regression proof:
- Required and observed build, packaging, or integration path:
- Focused commands and observed results:
- CI/build, packaged-artifact, deployment/runtime, or external evidence:
- Evidence ceiling and evidence not observed:
- Independent review subject/status, finding dispositions/promotions, and
  rollback when required:

## Review finding disposition

Replace every placeholder value in the active JSON record. Use either
`no_material_findings` or one or more `findings`, never both. Non-fixed findings require an identifiable human
owner and independent confirmer. Do not paste raw review transcripts, prompts,
credentials, private local paths, or secret-bearing payloads; use a redacted
summary and controlled evidence identifier.

<!-- ai-platform.review-findings.v1 -->
```json
{
  "schema_version": "ai-platform.review-findings.v1",
  "review_subject": {
    "head_sha": "REPLACE_ME_40_HEX_SHA",
    "scope": "REPLACE_ME_EXACT_REVIEW_SCOPE"
  },
  "no_material_findings": {
    "reviewer_handle": "@REPLACE_ME",
    "reviewer_role": "REPLACE_ME",
    "evidence_id": "REPLACE_ME"
  },
  "findings": []
}
```

When material findings exist, set `no_material_findings` to `null` and use this
shape for each item. A `rule` promotion additionally requires all seven
`rule_evidence` fields; other promotion targets use `rule_evidence: null`.

```json
{
  "id": "F-001",
  "severity": "P1",
  "summary": "Redacted summary of the verified finding",
  "evidence_id": "review-run-id:F-001",
  "disposition": "fixed",
  "owner": null,
  "defer_exit_condition": null,
  "independent_confirmation": {
    "handle": "@reviewer-handle",
    "role": "independent reviewer role",
    "evidence_id": "review-run-id",
    "head_sha": "40_lowercase_hex_reviewed_sha"
  },
  "promotion": {
    "target": "code",
    "rule_evidence": null
  }
}
```

Every finding's `independent_confirmation` contains `handle`, `role`,
`evidence_id`, and the reviewed `head_sha`. For `rejected_with_evidence` or
`deferred`, `owner` additionally contains a different human `handle` and
`role`. A deferred item also requires `defer_exit_condition`.

## Accuracy

- [ ] The actual diff stays within the declared or explicitly revised scope.
- [ ] Template text, checkboxes, mocks, and Agent self-report are not presented
      as assembled or external evidence.
- [ ] Pending, skipped, historical, different-SHA, source-only, and runtime
      evidence retain distinct labels.
- [ ] `Closes`/`Fixes` is used only when merge satisfies acceptance, review,
      and required runtime criteria.
