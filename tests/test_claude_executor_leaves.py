from __future__ import annotations

import ast
from pathlib import Path

from app.executors import claude_agent_sdk_runner as runner
from app.executors.claude import capability_policy, prompts


def test_runner_reexports_leaf_contracts_without_duplicate_implementations() -> None:
    assert runner.build_skill_prompt is prompts.build_skill_prompt
    assert runner._context_pack_prompt_section is prompts.context_pack_prompt_section
    assert (
        runner._prior_messages_prompt_section is prompts._prior_messages_prompt_section
    )
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
