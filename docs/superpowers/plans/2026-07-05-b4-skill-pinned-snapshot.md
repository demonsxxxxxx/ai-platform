# B4 Skill Pinned Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the B4-G5 source-level pinned Skill snapshot slice so queued and persisted run Skill snapshots carry a safe release/dependency/file governance summary.

**Architecture:** Add a deterministic safe summary builder in `app.skills.pinning`, attach summaries in `app.routes.runs` after final release locking, and persist them in `app.worker` under `source_json.snapshot_governance`. Keep repository projection sanitized and schema-free.

**Tech Stack:** Python, FastAPI route tests, worker unit tests, repository projection tests, pytest with workspace-local `--basetemp .pytest-tmp`.

## Global Constraints

- Do not add a database migration for this slice.
- Do not expose raw `release_decision`, `storage_key`, `content_base64`, `skill_version`, `content_hash`, `policy_active`, `channel`, `selected_version`, `selected_track`, `current_version`, `previous_version`, `fallback_version`, same-value digest, or rollout details inside `snapshot_governance`.
- Use schema version `ai-platform.skill-pinned-snapshot-governance.v1`.
- Set `snapshot_source` to `platform_release_lock`.
- Set `does_not_close_b4_or_211` to `true`.
- Every local pytest command must include `--basetemp .pytest-tmp`.
- Do not run full-repository pytest as a routine gate.
- Do not claim `reviewed`, `merged`, `211 verified`, or `gate closable` from source tests.

---

### Task 1: Pinning Governance Summary

**Files:**
- Modify: `app/skills/pinning.py`
- Test: `tests/test_skill_pinning.py`

**Interfaces:**
- Produces: `SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION: str`
- Produces: `build_skill_snapshot_governance(manifest: dict[str, Any], *, release_decision: dict[str, Any] | None = None) -> dict[str, Any]`
- Produces: `attach_skill_snapshot_governance(skill_manifests: list[dict[str, Any]], *, release_decision: dict[str, Any] | None = None) -> list[dict[str, Any]]`

- [ ] **Step 1: Add failing tests for safe summary shape**

Add tests to `tests/test_skill_pinning.py`:

```python
def test_build_skill_snapshot_governance_summarizes_files_without_package_bytes():
    from app.skills.pinning import build_skill_snapshot_governance

    pin = {
        "skill_id": "qa-file-reviewer",
        "version": "hash-primary",
        "content_hash": "hash-primary",
        "source": {
            "kind": "uploaded",
            "storage_key": "tenants/default/skills/qa-file-reviewer/package.zip",
        },
        "files": [
            {"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5},
            {"relative_path": "references/guide.md", "content_base64": "Z3VpZGU=", "size_bytes": 5},
        ],
        "dependency_ids": ["minimax-docx"],
    }

    governance = build_skill_snapshot_governance(
        pin,
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "policy_active": True,
            "selected_track": "current",
            "rollout_percent": 100,
            "channel": "stable",
            "selected_version": "hash-primary",
        },
    )

    assert governance["schema_version"] == "ai-platform.skill-pinned-snapshot-governance.v1"
    assert governance["snapshot_source"] == "platform_release_lock"
    assert governance["release_lock"] == {
        "schema_version": "ai-platform.skill-release-decision.v1",
        "mode": "release_policy",
    }
    assert governance["manifest"] == {
        "source_kind": "uploaded",
        "selected_file_count": 2,
    }
    assert governance["dependency_evidence"] == {
        "status": "review_required",
        "ref": "skill_dependency_policy",
        "dependency_count": 1,
    }
    assert [item["relative_path"] for item in governance["selected_files"]] == [
        "SKILL.md",
        "references/guide.md",
    ]
    assert governance["selected_files"][0]["sha256"] == "9c53c074d7ac6a2728b638ac1f376c5fa9eb8f71603017c3ea638c2fd40548df"
    assert governance["does_not_close_b4_or_211"] is True
    serialized = json.dumps(governance, ensure_ascii=False)
    assert "content_base64" not in serialized
    assert "storage_key" not in serialized
    assert "release_decision" not in serialized
    assert "selected_version" not in serialized
```

- [ ] **Step 2: Run failing pinning test**

Run:

```powershell
python -m pytest tests/test_skill_pinning.py::test_build_skill_snapshot_governance_summarizes_files_without_package_bytes -q --basetemp .pytest-tmp
```

Expected: fail because `build_skill_snapshot_governance` is not defined.

- [ ] **Step 3: Implement safe summary builder**

Add the functions in `app/skills/pinning.py`. The implementation must decode
each `content_base64`, compute `sha256`, and never copy raw file bytes or
storage keys into `snapshot_governance`.

- [ ] **Step 4: Run pinning tests**

Run:

```powershell
python -m pytest tests/test_skill_pinning.py -q --basetemp .pytest-tmp
```

Expected: all tests pass with existing symlink tests skipped when Windows
symlink support is unavailable.

### Task 2: Route Attachment

**Files:**
- Modify: `app/routes/runs.py`
- Test: `tests/test_routes.py`
- Test: `tests/test_run_control_routes.py`

**Interfaces:**
- Consumes: `attach_skill_snapshot_governance(skill_manifests, release_decision=release_decision_payload)`
- Produces: queue payload manifests that include top-level `snapshot_governance`.

- [ ] **Step 1: Add failing route tests**

Extend the create-run manifest pin test in `tests/test_routes.py` and the
copy-run queue test in `tests/test_run_control_routes.py` to assert:

```python
governance = calls["queue"]["skill_manifests"][0]["snapshot_governance"]
assert governance["schema_version"] == "ai-platform.skill-pinned-snapshot-governance.v1"
assert governance["snapshot_source"] == "platform_release_lock"
assert governance["does_not_close_b4_or_211"] is True
assert "release_decision" not in json.dumps(governance, ensure_ascii=False)
assert "content_base64" not in json.dumps(governance, ensure_ascii=False)
assert calls["queue"]["skill_version"] not in json.dumps(governance, ensure_ascii=False)
```

- [ ] **Step 2: Run failing route tests**

Run:

```powershell
python -m pytest tests/test_routes.py::test_create_run_uses_builtin_manifest_pin_for_locked_skill_version tests/test_run_control_routes.py::test_copy_run_creates_new_queued_run -q --basetemp .pytest-tmp
```

Expected: fail because manifests do not yet include `snapshot_governance`.

- [ ] **Step 3: Attach governance after release lock**

In `app/routes/runs.py`, import `attach_skill_snapshot_governance` and call it
after `release_decision_payload_for_locked_version(...)` in normal create-run
and copied-run flows.

- [ ] **Step 4: Run route tests**

Run:

```powershell
python -m pytest tests/test_routes.py::test_create_run_uses_builtin_manifest_pin_for_locked_skill_version tests/test_routes.py::test_create_run_uses_rollout_selected_previous_version tests/test_run_control_routes.py::test_copy_run_creates_new_queued_run -q --basetemp .pytest-tmp
```

Expected: selected tests pass.

### Task 3: Worker Persistence And Projection Safety

**Files:**
- Modify: `app/worker.py`
- Modify: `app/repositories.py` only if existing sanitization needs a small safe projection adjustment.
- Test: `tests/test_worker.py`
- Test: `tests/test_repositories.py`

**Interfaces:**
- Consumes: manifests with top-level `snapshot_governance`.
- Produces: `source_json` passed to `repositories.upsert_run_skill_snapshot` with `snapshot_governance` copied into source.

- [ ] **Step 1: Add failing worker and repository tests**

Add or extend tests to prove:

```python
assert snapshots[0]["source_json"]["snapshot_governance"]["schema_version"] == (
    "ai-platform.skill-pinned-snapshot-governance.v1"
)
assert "content_base64" not in json.dumps(snapshots[0]["source_json"], ensure_ascii=False)
```

For repository projection, add a `source_json` fixture containing safe
`snapshot_governance` plus forbidden nested keys, then assert the safe summary
survives and forbidden keys are removed from the returned snapshot.

- [ ] **Step 2: Run failing worker/repository tests**

Run:

```powershell
python -m pytest tests/test_worker.py::test_worker_persists_skill_snapshots_from_executor_payload tests/test_repositories.py::test_list_run_skill_snapshots_projects_persisted_telemetry -q --basetemp .pytest-tmp
```

Expected: fail until the worker persists `snapshot_governance`.

- [ ] **Step 3: Persist safe governance**

In `app/worker.py`, merge `snapshot_governance` into persisted `source_json`
from the result manifest when present, or from the queue payload manifest for
the same `skill_id` when the executor omits it. Do not persist `files` or
`content_base64`.

- [ ] **Step 4: Run worker and repository tests**

Run:

```powershell
python -m pytest tests/test_worker.py tests/test_repositories.py -q --basetemp .pytest-tmp
```

Expected: all selected tests pass.

### Task 4: Verification And Status Evidence

**Files:**
- Modify: `docs/operations/ai-platform-parallel-session-board.md`

**Interfaces:**
- Consumes: test outputs from Tasks 1-3.
- Produces: conservative final branch status no higher than `PR ready`.

- [ ] **Step 1: Run full targeted verification**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest tests/test_skill_pinning.py tests/test_routes.py tests/test_run_control_routes.py tests/test_worker.py tests/test_repositories.py -q --basetemp .pytest-tmp
python tools\skill_release_readiness.py --format json
git diff --check
```

Expected: compileall exits 0, pytest exits 0, readiness command exits 0, and
`git diff --check` exits 0.

- [ ] **Step 2: Update status board**

Set the B4 row status to `PR ready` only after verification and sub-agent
review are complete. If review is not complete, keep `local partial` and record
the latest evidence precisely.

- [ ] **Step 3: Run final self-review**

Check the diff for:

```text
No secrets, no real .env values, no personal paths in staged files.
New public functions have docstrings.
Tests cover happy path and redaction/error posture.
Status docs do not claim reviewed, merged, 211 verified, or gate closable.
```
