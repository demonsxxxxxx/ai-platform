# ai-platform Release Evidence Index

Date: 2026-06-08

This directory is the repository-owned location for reviewed, redacted release
evidence entries. It is an index and contract baseline only. This contract
does not close G9.

Generate the current readiness contract from the repository root:

```powershell
python tools/release_evidence_readiness.py --format markdown
python tools/release_evidence_readiness.py --format json
```

The readiness schema is `ai-platform.release-evidence-readiness.v1`. The entry
schema is `ai-platform.release-evidence-entry.v1`. The retention policy schema
is `ai-platform.release-evidence-retention-policy.v1`.

The G9 trace/audit export contract is generated separately:

```powershell
python tools/trace_audit_export_readiness.py --format markdown
python tools/trace_audit_export_readiness.py --format json
```

It uses schema `ai-platform.trace-audit-export-readiness.v1` and nested
contract schema `ai-platform.trace-audit-export-contract.v1`.

## Export Location

Release evidence entries belong under:

```text
docs/release-evidence/<gate>/<commit_sha>/<evidence_id>.json
```

The index for the evidence tree is:

```text
docs/release-evidence/README.md
```

Entries must be appended only after review and redaction. The evidence tree is
for public/admin operational proof, not executor private payloads, raw storage
keys, sandbox workdirs, secret material, bearer tokens, database URLs, or Redis
URLs.

For runtime smoke evidence, `commit_sha` is the verified subject commit that was
running or otherwise under review when the smoke was captured. The Git commit
that introduced or last updated an evidence file is intentionally not embedded
inside the JSON record, because a commit cannot contain its own final hash. Use
VCS history for the record commit. For `211_runtime_smoke` entries,
`runtime_subject_commit_sha`, `source_ref.runtime_source_marker`, and API/worker
image labels must all point to the same runtime subject commit.

## Reviewed Entries

| Date | Gate | Commit | Evidence | Status |
| --- | --- | --- | --- | --- |
| 2026-06-11 | Foundation Alpha POC | `8c0cffca63bc747fad0a5771f209acc8a608ab9e` | [`2026-06-11-211-foundation-alpha-poc-current-main-auth-rbac-smoke.json`](foundation-alpha-poc/8c0cffca63bc747fad0a5771f209acc8a608ab9e/2026-06-11-211-foundation-alpha-poc-current-main-auth-rbac-smoke.json) | Current main Auth/RBAC smoke passed on 211 against `ai-platform:8c0cffc-foundation-alpha-poc`; not production gate closure. |
| 2026-06-11 | Foundation Alpha POC | `8c0cffca63bc747fad0a5771f209acc8a608ab9e` | [`2026-06-11-211-foundation-alpha-poc-current-main-smoke.json`](foundation-alpha-poc/8c0cffca63bc747fad0a5771f209acc8a608ab9e/2026-06-11-211-foundation-alpha-poc-current-main-smoke.json) | Current main 211 runtime smoke passed for the controlled Foundation Alpha POC loop; not production gate closure. |
| 2026-06-11 | Foundation Alpha POC | `bf20432f9889efa8b367afdf512c641068ba30bc` | [`2026-06-11-211-foundation-alpha-poc-auth-rbac-smoke.json`](foundation-alpha-poc/bf20432f9889efa8b367afdf512c641068ba30bc/2026-06-11-211-foundation-alpha-poc-auth-rbac-smoke.json) | Auth/RBAC smoke passed against the existing merged runtime using verifier source `3a3da257484d4d430a7a26e00a6f1cdae39a2b12`; not production gate closure. |
| 2026-06-11 | Foundation Alpha POC | `bf20432f9889efa8b367afdf512c641068ba30bc` | [`2026-06-11-211-foundation-alpha-poc-merged-smoke.json`](foundation-alpha-poc/bf20432f9889efa8b367afdf512c641068ba30bc/2026-06-11-211-foundation-alpha-poc-merged-smoke.json) | Merged main 211 runtime smoke passed for the controlled POC loop; not production gate closure. |
| 2026-06-11 | Foundation Alpha POC | `3874281276c84a418bd08bda56d7ea55b52970b7` | [`2026-06-11-211-foundation-alpha-poc-smoke.json`](foundation-alpha-poc/3874281276c84a418bd08bda56d7ea55b52970b7/2026-06-11-211-foundation-alpha-poc-smoke.json) | 211 runtime smoke passed for the controlled POC loop; not production gate closure. |

## Entry Contract

Each `ai-platform.release-evidence-entry.v1` entry must include:

- `evidence_id`
- `commit_sha`
- `gate`
- `issue_refs`
- `artifact_kind`
- `captured_at`
- `source_ref`
- `evidence_ref`
- `redaction_scan_status`
- `review_status`

Conditional fields:

- `211_runtime_smoke`: `runtime_subject_commit_sha`

Accepted artifact kinds currently include:

- `211_runtime_smoke`
- `capacity_gate_readiness`
- `frontend_packaged_runtime_smoke`
- `frontend_release_traceability`
- `governance_readiness`
- `observability_readiness`

## Trace / Audit Export Contract

Future reviewed trace/audit export evidence belongs to the platform audit
domain path:

```text
audit.trace_exports.<export_id>
```

The `trace_audit_export_contract` allows only public/admin projection sources:

- `run_event_public_projection`
- `audit_event_public_projection`
- `admin_runtime_observability_summary`
- `release_evidence_entry`

This is a contract-only baseline. It does not export runtime data and does not
close G9. Remaining blockers are:

- `trace_audit_export_runtime_acceptance`
- `trace_audit_export_dashboard_acceptance`
- `trace_audit_export_211_acceptance`

## Retention Policy Contract

Release evidence currently has a source-level retention policy contract only.
It is not runtime-enforced and does not close G9.

- schema: `ai-platform.release-evidence-retention-policy.v1`
- status: `contract_only_not_runtime_enforced`
- default retention: 180 days
- minimum retention: 30 days
- delete requires review and applies only to reviewed, redacted evidence
  entries

The policy forbids deleting raw runtime payloads, executor private payloads,
raw storage keys, sandbox work directories, secret material, or unreviewed
evidence drafts through this reviewed release-evidence path.

## Open Gaps

- `release_evidence_runtime_export_acceptance`
- `release_evidence_retention_runtime_acceptance`

This contract creates the source-level export location and entry shape. It does
not close the gate until runtime export acceptance, runtime retention
acceptance, review workflow, and deployment evidence are all proven.
