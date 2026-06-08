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

Accepted artifact kinds currently include:

- `211_runtime_smoke`
- `capacity_gate_readiness`
- `frontend_packaged_runtime_smoke`
- `frontend_release_traceability`
- `governance_readiness`
- `observability_readiness`

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
