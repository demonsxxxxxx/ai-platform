# S0B File Artifact Backend Boundary Phase Status

Status: `implementation complete / local verification green / review substitute handled / pr open / ci pending`

## Scope

- Issue: `#377` S0B backend boundary hardening for files and artifacts.
- Current lane owns only:
  - `app/routes/files.py`
  - `tests/test_artifact_permissions.py`
  - `tests/test_frontend_projection_routes.py`
  - dedicated upload security tests for this slice
  - this phase-status document
- Explicit non-scope: `frontend/**`, `app/auth.py`, `app/schema.sql`, `app/repositories.py`, `app/routes/runs.py`, `app/worker.py`, `tests/test_routes.py`, `tests/test_repositories.py`, `tests/test_schema.py`, `tests/test_worker.py`, `deploy/**`, and CI workflow files.

## Phase Matrix

- [x] Phase 0: Source lane and issue boundary established. Evidence: worktree started from `289897087ce3b88724401f78b936f96fa7b68562`, local branch `codex/s0b-file-artifact-boundary` created from detached HEAD, and GitHub issue `#377` records scope, acceptance, verification, and boundary constraints.
- [x] Phase 1: RED tests captured missing permission gates for artifact download/preview and upload before DB/body/storage access. Evidence: initial RED runs failed because `app/routes/files.py` still reached artifact repository lookups and object storage without formal permission gates; upload security RED also showed missing permission checks still fell through to object storage.
- [x] Phase 2: RED tests captured bounded upload reads, active-content fail-closed behavior, and ZIP/OOXML structural validation. Evidence: initial RED runs showed `file.read()` still used an unbounded read path and active-content / ZIP cases still reached storage writes.
- [x] Phase 3: Route implementation in `app/routes/files.py` now satisfies the backend boundary without changing public permission names or storage-key behavior. Evidence: `files.py` now enforces `artifact:download` and upload permissions before artifact lookup/body read, uses bounded `MAX_UPLOAD_BYTES + 1` reads, rejects active content by extension/MIME/sniff, and validates ZIP/OOXML structure before storage writes while preserving the existing storage-key pattern `tenants/<tenant>/workspaces/<workspace>/sessions/<session>/files/<file_id>/<safe_name>`.
- [x] Phase 4: Compat upload contract remained stable after routing through the hardened backend upload path. Evidence: `tests/test_file_upload_security.py::test_compat_upload_preserves_frontend_response_contract` passed with the expected `key`, `file_id`, `url`, `name`, `type`, `mimeType`, `mime_type`, `size`, and `sha256` fields.
- [x] Phase 5: Focused local verification, `git diff --check`, and self-review passed. Evidence:
  - `python -m compileall -q app tools scripts`
  - `python -m pytest tests/test_file_upload_security.py -q --basetemp .pytest-tmp\\s0b-bom-green`
  - `python -m pytest tests/test_artifact_permissions.py tests/test_file_upload_security.py tests/test_lambchat_frontend_compat.py tests/test_two_user_artifact_isolation.py tests/test_frontend_projection_routes.py tests/test_contract.py -q --basetemp .pytest-tmp\\s0b-bom-postfix-verify`
  - `git diff --check`
- [x] Phase 6: Independent sub-agent review substitute and re-review are posted and fully handled. Evidence: initial review found a UTF-8 BOM active-content sniff bypass in `app/routes/files.py`; the first fix added BOM-aware sniffing plus UTF-8 regression coverage in `tests/test_file_upload_security.py`; a follow-up gate then required explicit UTF-16LE/BE and UTF-32LE/BE BOM regressions plus no repository/storage side effects, after which a fresh review returned `No findings`.
- [~] Phase 7: PR is open with validation evidence posted; observed CI is still pending. Evidence: ready PR `#378` is open on `codex/s0b-file-artifact-boundary`, both `review substitute` and `validation evidence` comments are posted, and the currently observed GitHub checks are `backend required` and `projection audit, lint, build, trace` in progress.

## Current Notes

- The approved design boundary is the in-thread contract plus issue `#377`; no extra S0B spec/plan files are used for this lane.
- Compat upload remains covered through the hardened `files.py` path; this lane does not own `app/routes/lambchat_compat.py`.
- This slice must not claim 211 verification, S0 closure, S2 closure, or full security closure.
