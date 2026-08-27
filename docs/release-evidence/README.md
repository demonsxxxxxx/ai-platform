# Release Evidence Contract

This directory stores reviewed, redacted evidence. It is not a current-status
board and it cannot prove a later source revision or deployed runtime.

## Entry Contract

Use the structure:

```text
docs/release-evidence/<gate>/<commit_sha>/<evidence_id>.json
```

An entry uses `ai-platform.release-evidence-entry.v1`, is added only when a
formal release, controlled runtime acceptance, or durable audit requirement
needs a reviewed record, and contains no credentials, raw environment values,
private executor payloads, storage keys, sandbox work directories, or absolute
private paths. Ordinary pull requests, local tests, CI jobs, and review comments
store their transient output in GitHub checks or Actions artifacts instead of
adding repository evidence. Evidence ingestion and safe-index behavior are
implemented by:

```powershell
python tools/release_evidence_readiness.py --format json
python tools/release_evidence_export_acceptance.py --format json
python tools/verify_release_evidence_runtime_acceptance.py --format json
```

These commands are source checks. They do not deploy, rerun an acceptance probe,
or make a gate current.

Keep reviewed evidence machine-readable. Do not commit generated Markdown
readiness or status summaries beside evidence JSON; render operator-readable
output from the source check when it is needed. Human-authored Markdown in this
directory is limited to durable contracts and provenance records.

## Interpretation

Runtime-bound evidence must bind its `runtime_subject_commit_sha`, source
marker, and OCI revision labels to the observed subject. When several reviewed
entries exist, readiness tools select evidence according to their implemented
subject-matching rules; an older entry remains historical. Only a fresh
authorized runtime procedure can establish `211 verified` for its exact
deployed subject.

Keep diagnostics separate from reviewed evidence. A diagnostic may explain a
failure or a fix direction, but cannot close a gate. Deletion or retention of a
reviewed entry follows the machine-readable retention policy and review process,
not a documentation cleanup.
