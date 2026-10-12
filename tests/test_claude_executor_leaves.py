from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from app.executors import claude_agent_sdk_runner as runner
from app.executors.claude import capability_policy, prompts


def test_runner_reexports_leaf_contracts_without_duplicate_implementations() -> None:
    assert runner.build_skill_prompt is prompts.build_skill_prompt
    assert runner._context_pack_prompt_section is prompts.context_pack_prompt_section
    assert runner.CapabilityExecutionPlan is capability_policy.CapabilityExecutionPlan
    assert (
        runner.internal_context_tool_policy_subjects
        is capability_policy.internal_context_tool_policy_subjects
    )


def test_leaf_modules_do_not_import_orchestration_owners() -> None:
    forbidden = (
        "app.repositories",
        "app.routes",
        "app.worker",
        "app.runtime.sandbox",
    )
    for module in (prompts, capability_policy):
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        imported.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(
            dependency == owner or dependency.startswith(f"{owner}.")
            for dependency in imported
            for owner in forbidden
        )


def test_leaf_import_closure_excludes_orchestration_owners() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys; "
            "from app.executors.claude import capability_policy, prompts; "
            "print(json.dumps(sorted(sys.modules)))",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    forbidden = ("app.repositories", "app.routes", "app.worker", "app.runtime.sandbox")
    assert not [
        dependency
        for dependency in json.loads(completed.stdout)
        if any(dependency == owner or dependency.startswith(f"{owner}.") for owner in forbidden)
    ]


def test_profile_drive_capability_contract_preserves_wire_and_legacy_imports() -> None:
    from app.files import api as files
    from app.runtime.sandbox import contracts

    expected = {
        "PROFILE_DRIVE_STAGE_TOOL": "stage_profile_drive_file_to_workspace",
        "PROFILE_DRIVE_STAGE_IDENTITY": "mcp__ai-platform-context__stage_profile_drive_file_to_workspace",
        "PROFILE_DRIVE_READ_TEXT_IDENTITY": "mcp__ProfileDriveMCPServer__read_text_file",
    }
    for name, wire_value in expected.items():
        assert getattr(files, name) == wire_value
        assert getattr(contracts, name) is getattr(files, name)
    assert capability_policy.PROFILE_DRIVE_STAGE_TOOL is files.PROFILE_DRIVE_STAGE_TOOL
    assert capability_policy.PROFILE_DRIVE_READ_TEXT_IDENTITY is files.PROFILE_DRIVE_READ_TEXT_IDENTITY
