from types import SimpleNamespace

import pytest

from app.executors.claude_agent_sdk_runner import _workspace_path_parameters_authorized
from app.sandbox.domain.workspace_policy import (
    workspace_collection_file_allowed,
    workspace_read_allowed,
)
from app.required_tool_contract import builtin_capability_subjects
from app.skills.execution_profiles import (
    effective_skill_execution_profile,
    resolve_skill_execution_profile,
)


@pytest.mark.parametrize("path", ["outputs/logs/job.txt", "scripts/runtime/helper.py", "outputs/_audit/report.txt"])
def test_task_owned_nested_directories_remain_readable_and_deliverable(path):
    assert workspace_read_allowed(path)
    assert workspace_collection_file_allowed(path)


@pytest.mark.parametrize("path", [".ai-platform/token", ".claude-config/settings.json", "logs/runtime.log", "../other-tenant/data.txt"])
def test_platform_roots_and_parent_workspace_remain_private(path):
    assert not workspace_read_allowed(path)
    assert not workspace_collection_file_allowed(path)


@pytest.mark.parametrize("pattern", ["**/*.{py,md}", "reports/result[0-9]?.txt", "outputs/logs/*.txt"])
def test_sdk_accepts_scoped_native_glob_syntax(tmp_path, pattern):
    assert _workspace_path_parameters_authorized(
        {"workspace_contract": "ai-platform.skill-workspace.v1"},
        "Glob", {"pattern": pattern, "path": "."}, workspace_root=tmp_path,
    )


def test_search_scope_rejects_symlink_escape_with_valid_pattern(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "escape").symlink_to(tmp_path, target_is_directory=True)
    assert not _workspace_path_parameters_authorized(
        {"workspace_contract": "ai-platform.skill-workspace.v1"},
        "Glob", {"pattern": "*.{py,md}", "path": "escape"}, workspace_root=workspace,
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_skill_set_local_tool_policy_is_independent_of_primary_order(reverse):
    manifests = []
    for skill_id, status in [("restricted", "active"), ("released", "released")]:
        manifest = {"skill_id": skill_id, "source": {"kind": "uploaded"}, "lifecycle_status": status}
        manifest["execution_profile"] = resolve_skill_execution_profile(
            skill_id=skill_id, source_kind="uploaded", lifecycle_status=status,
        )
        manifests.append(manifest)
    if reverse:
        manifests.reverse()
    subjects = builtin_capability_subjects(
        payload=SimpleNamespace(input={}, skill_manifests=manifests),
        run_identity={"skill_id": manifests[0]["skill_id"]},
        skill={"skill_status": "active"}, skill_decision=SimpleNamespace(usable=True),
        canonical_manifest=effective_skill_execution_profile,
        authorized_skill_manifests=manifests,
        authorized_skill_names=[manifest["skill_id"] for manifest in manifests],
    )
    skill_subject = next(subject for subject in subjects if subject["identity"] == "Skill")
    assert skill_subject["execution_strategy"] == "sandbox_full_local"
    assert set(skill_subject["allowed_skill_names"]) == {"restricted", "released"}
