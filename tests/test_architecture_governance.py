from __future__ import annotations

import ast
import importlib.util
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "architecture_governance.py"
POLICY_PATH = REPO_ROOT / "architecture-policy.json"
SCHEMA_PATH = REPO_ROOT / "schemas" / "architecture-policy.v1.schema.json"
SPEC = importlib.util.spec_from_file_location("architecture_governance_under_test", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
architecture_governance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = architecture_governance
SPEC.loader.exec_module(architecture_governance)


def _run(repo: Path, *command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )


def _git(repo: Path, *arguments: str) -> str:
    return _run(repo, "git", *arguments).stdout.strip()


def _write(repo: Path, path: str, content: str) -> None:
    destination = repo / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _retired_runs_legacy_api_cutover() -> dict[str, Any]:
    return {
        "source_path": "app/repositories.py",
        "public_module": "app.runs.api",
        "canonical_module": "app.runs.domain.terminalization",
        "module_alias": "runs_api",
        "removed_imports": [{"module": "dataclasses", "name": "dataclass"}],
        "rewrites": [
            {
                "old_symbol": "TERMINAL_RUN_STATUSES",
                "new_symbol": "TERMINAL_RUN_STATUSES",
            },
            {
                "old_symbol": "ToolPermissionTerminalizationProgress",
                "new_symbol": "RunTerminalizationProgress",
            },
            {
                "old_symbol": "_terminalization_progress_for_requested_status",
                "new_symbol": "progress_for_requested_status",
            },
        ],
        "owner": "runs",
        "reason": (
            "The frozen global repository may perform one exact hard cut from its "
            "locally owned Run terminalization policy symbols to the public Runs API "
            "without retaining policy aliases or importing Runs internals."
        ),
        "removal_condition": (
            "After the exact Runs policy cutover is merged, remove this consumed "
            "authority entry before any further app/repositories.py source change."
        ),
    }


def _fixture_policy() -> dict[str, Any]:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy["legacy_api_cutovers"] = [_retired_runs_legacy_api_cutover()]
    return policy


def _migration_bridge(*, source_path: str, target_module: str) -> dict[str, Any]:
    return next(
        bridge
        for bridge in _fixture_policy()["migration_bridges"]
        if bridge["source_path"] == source_path
        and bridge["target_module"] == target_module
    )


def _legacy_api_cutover(*, source_path: str = "app/repositories.py") -> dict[str, Any]:
    return next(
        cutover
        for cutover in _fixture_policy()["legacy_api_cutovers"]
        if cutover["source_path"] == source_path
    )


def _fixture_async_definitions(symbols: list[str]) -> str:
    return "\n\n".join(
        f"async def {name}():\n    marker = {name!r}\n    return marker"
        for name in symbols
    )


def _fixture_bridge_definitions(symbols: list[str]) -> str:
    definitions: list[str] = []
    for name in symbols:
        if name.isupper():
            definitions.append(f"{name} = 1")
        elif name[:1].isupper():
            definitions.append(f"class {name}:\n    pass")
        else:
            definitions.append(
                f"async def {name}():\n    marker = {name!r}\n    return marker"
            )
    return "\n\n".join(definitions)


def _fixture_cutover_suffix(source_path: str) -> str:
    cutover = next(
        (
            item
            for item in _fixture_policy()["legacy_api_cutovers"]
            if item["source_path"] == source_path
        ),
        None,
    )
    if cutover is None:
        return ""
    removed_imports = "\n".join(
        f"from {item['module']} import {item['name']}"
        for item in cutover["removed_imports"]
    )
    old_symbols = [item["old_symbol"] for item in cutover["rewrites"]]
    definitions = _fixture_bridge_definitions(old_symbols)
    uses = ", ".join(old_symbols)
    return f"{removed_imports}\n\n{definitions}\n\ndef cutover_usage():\n    return ({uses})\n"


def _create_repo(
    tmp_path: Path,
    *,
    policy_text: str | None = None,
    schema_text: str | None = None,
) -> tuple[Path, str]:
    repo = tmp_path / "repository"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Architecture Tests")
    _git(repo, "config", "user.email", "architecture-tests@example.com")
    _write(repo, "tools/architecture_governance.py", TOOL_PATH.read_text(encoding="utf-8"))
    _write(
        repo,
        "schemas/architecture-policy.v1.schema.json",
        schema_text if schema_text is not None else SCHEMA_PATH.read_text(encoding="utf-8"),
    )
    _write(
        repo,
        "architecture-policy.json",
        (
            policy_text
            if policy_text is not None
            else json.dumps(_fixture_policy(), indent=2, sort_keys=True) + "\n"
        ),
    )
    _write(repo, "docs/architecture/source-code-architecture.md", "# Source architecture\n")
    for path in _fixture_policy()["approved_root_modules"]:
        _write(repo, path, "")
    for path in (
        "app/executors/claude_agent_worker.py",
        "app/models.py",
        "app/repositories.py",
        "app/routes/chat.py",
        "app/runtime/sandbox/container_provider.py",
        "app/worker.py",
    ):
        _write(repo, path, "BASELINE = True\n")
    _write(
        repo,
        "app/artifact_lifecycle_repository.py",
        "from app.persistence.object_deletions import claim_object_deletions\n\n"
        "__all__ = [\"claim_object_deletions\"]\n",
    )
    _write(
        repo,
        "app/executors/registry.py",
        "from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter\n"
        "def _default_adapters():\n"
        "    return {\"claude-agent-worker\": ClaudeAgentWorkerAdapter()}\n",
    )
    fixture_bridges = _fixture_policy()["migration_bridges"]
    for source_path in sorted({bridge["source_path"] for bridge in fixture_bridges}):
        symbols = sorted(
            {
                symbol
                for bridge in fixture_bridges
                if bridge["source_path"] == source_path
                for symbol in bridge["symbols"]
            }
        )
        prefix = (
            "DEFAULT_RUN_EXECUTOR_TYPES = {\"claude-agent-worker\"}\n\n"
            if source_path == "app/repositories.py"
            else ""
        )
        _write(repo, source_path, prefix + _fixture_bridge_definitions(symbols) + "\n")
    for source_path in sorted(
        {item["source_path"] for item in _fixture_policy()["legacy_api_cutovers"]}
    ):
        current = (repo / source_path).read_text(encoding="utf-8")
        _write(repo, source_path, current + _fixture_cutover_suffix(source_path))
    _write(
        repo,
        "app/persistence/object_deletions.py",
        "OUTBOX_TARGET_ARTIFACT = \"artifact\"\nOUTBOX_TARGET_FILE = \"file\"\n",
    )
    _write(repo, "app/auth.py", "BASELINE = True\n")
    authority = _commit(repo, "trusted architecture authority")
    return repo, authority


@pytest.fixture
def governance_repo(tmp_path: Path) -> tuple[Path, str]:
    return _create_repo(tmp_path)


def _evaluate(
    repo: Path,
    authority: str,
    base: str,
    head: str,
    *,
    today: date = date(2026, 8, 13),
) -> Any:
    return architecture_governance.ArchitectureEvaluator(
        repo,
        today=today,
        tool_path=TOOL_PATH,
    ).evaluate(authority, base, head)


def _codes(evaluation: Any) -> set[str]:
    return {finding.code for finding in evaluation.findings}


def _activate_agent_profile_bridge(repo: Path, *, source_suffix: str = "") -> None:
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.agent_apps.infrastructure.postgres",
    )
    symbols = bridge["symbols"]
    remaining_symbols = sorted(
        {
            symbol
            for other in _fixture_policy()["migration_bridges"]
            if other["source_path"] == bridge["source_path"] and other != bridge
            for symbol in other["symbols"]
        }
    )
    _write(
        repo,
        "app/agent_apps/infrastructure/postgres.py",
        _fixture_async_definitions(symbols) + "\n",
    )
    _write(
        repo,
        bridge["source_path"],
        f"import {bridge['target_module']} as {bridge['module_alias']}\n"
        "DEFAULT_RUN_EXECUTOR_TYPES = {\"claude-agent-worker\"}\n"
        + _fixture_bridge_definitions(remaining_symbols)
        + "\n"
        + "\n".join(
            f"{name} = {bridge['module_alias']}.{name}" for name in symbols
        )
        + f"\n{source_suffix}"
        + _fixture_cutover_suffix(bridge["source_path"]),
    )


def _activate_context_memory_bridge(repo: Path) -> None:
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.context.infrastructure.postgres",
    )
    remaining_symbols = sorted(
        {
            symbol
            for other in _fixture_policy()["migration_bridges"]
            if other["source_path"] == bridge["source_path"] and other != bridge
            for symbol in other["symbols"]
        }
    )
    _write(
        repo,
        "app/context/infrastructure/postgres.py",
        _fixture_bridge_definitions(bridge["symbols"]) + "\n",
    )
    _write(
        repo,
        bridge["source_path"],
        f"import {bridge['target_module']} as {bridge['module_alias']}\n"
        "DEFAULT_RUN_EXECUTOR_TYPES = {\"claude-agent-worker\"}\n"
        + _fixture_bridge_definitions(remaining_symbols)
        + "\n"
        + "\n".join(
            f"{name} = {bridge['module_alias']}.{name}"
            for name in bridge["symbols"]
        )
        + "\n"
        + _fixture_cutover_suffix(bridge["source_path"]),
    )


def _activate_memory_redaction_bridge(repo: Path) -> None:
    bridge = _migration_bridge(
        source_path="app/memory_redaction.py",
        target_module="app.kernel.memory_redaction",
    )
    _write(
        repo,
        "app/kernel/memory_redaction.py",
        _fixture_bridge_definitions(bridge["symbols"]) + "\n",
    )
    _write(
        repo,
        bridge["source_path"],
        f"import {bridge['target_module']} as {bridge['module_alias']}\n"
        + "\n".join(
            f"{name} = {bridge['module_alias']}.{name}"
            for name in bridge["symbols"]
        )
        + "\n",
    )


def _activate_conversation_bridges(repo: Path) -> None:
    bridges = [
        _migration_bridge(
            source_path="app/agent_conversation_repository.py",
            target_module="app.conversations.infrastructure.postgres",
        ),
        _migration_bridge(
            source_path="app/repositories.py",
            target_module="app.conversations.infrastructure.postgres",
        ),
    ]
    target_symbols = sorted(
        {symbol for bridge in bridges for symbol in bridge["symbols"]}
    )
    _write(
        repo,
        "app/conversations/infrastructure/postgres.py",
        _fixture_async_definitions(target_symbols) + "\n",
    )
    for bridge in bridges:
        remaining_symbols = sorted(
            {
                symbol
                for other in _fixture_policy()["migration_bridges"]
                if other["source_path"] == bridge["source_path"]
                and other["target_module"] != bridge["target_module"]
                for symbol in other["symbols"]
            }
        )
        prefix = (
            "DEFAULT_RUN_EXECUTOR_TYPES = {\"claude-agent-worker\"}\n"
            if bridge["source_path"] == "app/repositories.py"
            else ""
        )
        local_definitions = _fixture_bridge_definitions(remaining_symbols)
        if local_definitions:
            local_definitions += "\n"
        aliases = "\n".join(
            f"{name} = {bridge['module_alias']}.{name}"
            for name in bridge["symbols"]
        )
        _write(
            repo,
            bridge["source_path"],
            f"import {bridge['target_module']} as {bridge['module_alias']}\n"
            + prefix
            + local_definitions
            + aliases
            + "\n"
            + _fixture_cutover_suffix(bridge["source_path"]),
        )


def _activate_runs_bridge(repo: Path) -> None:
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.runs.infrastructure.postgres",
    )
    remaining_symbols = sorted(
        {
            symbol
            for other in _fixture_policy()["migration_bridges"]
            if other["source_path"] == bridge["source_path"] and other != bridge
            for symbol in other["symbols"]
        }
    )
    _write(
        repo,
        "app/runs/infrastructure/postgres.py",
        _fixture_async_definitions(bridge["symbols"]) + "\n",
    )
    _write(
        repo,
        bridge["source_path"],
        f"import {bridge['target_module']} as {bridge['module_alias']}\n"
        "DEFAULT_RUN_EXECUTOR_TYPES = {\"claude-agent-worker\"}\n"
        + _fixture_bridge_definitions(remaining_symbols)
        + "\n"
        + "\n".join(
            f"{name} = {bridge['module_alias']}.{name}"
            for name in bridge["symbols"]
        )
        + "\n"
        + _fixture_cutover_suffix(bridge["source_path"]),
    )


def _activate_repository_authorization_error_bridge(repo: Path) -> None:
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.platform.postgres.errors",
    )
    remaining_symbols = sorted(
        {
            symbol
            for other in _fixture_policy()["migration_bridges"]
            if other["source_path"] == bridge["source_path"] and other != bridge
            for symbol in other["symbols"]
        }
    )
    _write(
        repo,
        "app/platform/postgres/errors.py",
        _fixture_bridge_definitions(bridge["symbols"]) + "\n",
    )
    _write(
        repo,
        bridge["source_path"],
        f"import {bridge['target_module']} as {bridge['module_alias']}\n"
        "DEFAULT_RUN_EXECUTOR_TYPES = {\"claude-agent-worker\"}\n"
        + _fixture_bridge_definitions(remaining_symbols)
        + "\n"
        + "\n".join(
            f"{name} = {bridge['module_alias']}.{name}"
            for name in bridge["symbols"]
        )
        + "\n"
        + _fixture_cutover_suffix(bridge["source_path"]),
    )


def _activate_all_migration_bridges(repo: Path) -> None:
    bridges = _fixture_policy()["migration_bridges"]
    for target_module in sorted({bridge["target_module"] for bridge in bridges}):
        symbols = sorted(
            {
                symbol
                for bridge in bridges
                if bridge["target_module"] == target_module
                for symbol in bridge["symbols"]
            }
        )
        _write(
            repo,
            f"{target_module.replace('.', '/')}.py",
            _fixture_bridge_definitions(symbols) + "\n",
        )
    for source_path in sorted({bridge["source_path"] for bridge in bridges}):
        source_bridges = sorted(
            (bridge for bridge in bridges if bridge["source_path"] == source_path),
            key=lambda bridge: bridge["target_module"],
        )
        imports = "\n".join(
            f"import {bridge['target_module']} as {bridge['module_alias']}"
            for bridge in source_bridges
        )
        prefix = (
            "DEFAULT_RUN_EXECUTOR_TYPES = {\"claude-agent-worker\"}\n"
            if source_path == "app/repositories.py"
            else ""
        )
        aliases = "\n".join(
            f"{symbol} = {bridge['module_alias']}.{symbol}"
            for bridge in source_bridges
            for symbol in bridge["symbols"]
        )
        _write(
            repo,
            source_path,
            f"{imports}\n{prefix}{aliases}\n" + _fixture_cutover_suffix(source_path),
        )


def _activate_legacy_api_cutover(repo: Path) -> None:
    cutover = _legacy_api_cutover()
    source_path = repo / cutover["source_path"]
    suffix = _fixture_cutover_suffix(cutover["source_path"])
    source = source_path.read_text(encoding="utf-8")
    assert source.endswith(suffix)
    legacy_prefix = source[: -len(suffix)]
    target_definitions = _fixture_bridge_definitions(
        [rewrite["new_symbol"] for rewrite in cutover["rewrites"]]
    )
    domain_module = "app/runs/domain/terminalization.py"
    _write(repo, domain_module, target_definitions + "\n")
    imported = ", ".join(
        f"{name} as {name}"
        for name in sorted(rewrite["new_symbol"] for rewrite in cutover["rewrites"])
    )
    _write(
        repo,
        "app/runs/api.py",
        f"from {cutover['canonical_module']} import {imported}\n",
    )
    uses = ", ".join(
        f"{cutover['module_alias']}.{rewrite['new_symbol']}"
        for rewrite in cutover["rewrites"]
    )
    _write(
        repo,
        cutover["source_path"],
        legacy_prefix
        + f"import {cutover['public_module']} as {cutover['module_alias']}\n\n"
        + f"def cutover_usage():\n    return ({uses})\n",
    )


def test_policy_and_schema_are_closed_sorted_authority_contracts(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "README.md", "candidate\n")
    head = _commit(repo, "candidate")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert evaluation.policy == {
        "owner": "platform-architecture",
        "path": "architecture-policy.json",
        "schema_path": "schemas/architecture-policy.v1.schema.json",
        "schema_version": "ai-platform.architecture-policy.v1",
        "source_contract": "docs/architecture/source-code-architecture.md",
    }


def test_authority_can_retire_the_last_migration_bridge(tmp_path: Path) -> None:
    policy = _fixture_policy()
    policy["migration_bridges"] = []
    policy["legacy_api_cutovers"] = []
    repo, authority = _create_repo(tmp_path, policy_text=json.dumps(policy))

    evaluation = _evaluate(repo, authority, authority, authority)

    assert evaluation.status == "pass"


def test_authority_can_retire_the_last_legacy_api_cutover(tmp_path: Path) -> None:
    policy = _fixture_policy()
    policy["legacy_api_cutovers"] = []
    repo, authority = _create_repo(tmp_path, policy_text=json.dumps(policy))

    evaluation = _evaluate(repo, authority, authority, authority)

    assert evaluation.status == "pass"


def test_live_authority_has_retired_legacy_api_cutovers() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    assert policy["legacy_api_cutovers"] == []


@pytest.mark.parametrize("value", ["main", "ABCDEF", "0" * 39, "A" * 40])
def test_refs_must_be_lowercase_full_commit_ids(
    governance_repo: tuple[Path, str], value: str
) -> None:
    repo, authority = governance_repo

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, value, authority, authority)

    assert caught.value.code == "invalid_ref"


def test_missing_full_ref_fails_closed(governance_repo: tuple[Path, str]) -> None:
    repo, authority = governance_repo

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, "0" * 40, authority, authority)

    assert caught.value.code == "missing_ref"


def test_non_ancestor_candidate_range_fails_closed(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "first.txt", "first\n")
    base = _commit(repo, "first branch")
    _git(repo, "checkout", "-b", "other", authority)
    _write(repo, "other.txt", "other\n")
    head = _commit(repo, "other branch")

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, authority, base, head)

    assert caught.value.code == "non_ancestor_range"


def test_authority_must_precede_base(governance_repo: tuple[Path, str]) -> None:
    repo, authority = governance_repo
    _write(repo, "base.txt", "base\n")
    base = _commit(repo, "base")

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, base, authority, base)

    assert caught.value.code == "authority_not_ancestor"


def test_checker_source_must_match_authority_object(
    governance_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, authority = governance_repo
    altered = tmp_path / "altered.py"
    altered.write_text(TOOL_PATH.read_text(encoding="utf-8") + "\n# altered\n", encoding="utf-8")

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        architecture_governance.ArchitectureEvaluator(repo, tool_path=altered).evaluate(
            authority,
            authority,
            authority,
        )

    assert caught.value.code == "authority_source_mismatch"


def test_duplicate_policy_key_is_rejected_before_candidate_evaluation(tmp_path: Path) -> None:
    original = POLICY_PATH.read_text(encoding="utf-8")
    duplicate = original.replace(
        '  "owner": "platform-architecture",',
        '  "owner": "platform-architecture",\n  "owner": "candidate-owner",',
        1,
    )
    repo, authority = _create_repo(tmp_path, policy_text=duplicate)

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, authority, authority, authority)

    assert caught.value.code == "invalid_policy"
    assert "duplicate JSON key" in str(caught.value)


def test_malformed_authority_schema_is_rejected(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["additionalProperties"] = True
    repo, authority = _create_repo(tmp_path, schema_text=json.dumps(schema))

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, authority, authority, authority)

    assert caught.value.code == "invalid_policy_schema"


def test_authority_schema_is_applied_to_policy_entries(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["$defs"]["hotFile"]["properties"]["max_lines"]["maximum"] = 10
    repo, authority = _create_repo(tmp_path, schema_text=json.dumps(schema))

    with pytest.raises(architecture_governance.ArchitectureError, match="schema maximum") as caught:
        _evaluate(repo, authority, authority, authority)

    assert caught.value.code == "invalid_policy"


def test_authority_schema_rejects_unapproved_patterns(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["$defs"]["nonEmptyString"]["pattern"] = "platform"
    repo, authority = _create_repo(tmp_path, schema_text=json.dumps(schema))

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, authority, authority, authority)

    assert caught.value.code == "invalid_policy_schema"


def test_authority_schema_patterns_have_a_bounded_length(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["$defs"]["nonEmptyString"]["pattern"] = "a" * 513
    repo, authority = _create_repo(tmp_path, schema_text=json.dumps(schema))

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, authority, authority, authority)

    assert caught.value.code == "invalid_policy_schema"


def test_authority_schema_rejects_nested_quantified_patterns(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["$defs"]["nonEmptyString"]["pattern"] = "(a+)+$"
    policy = _fixture_policy()
    policy["owner"] = "a" * 30 + "!"
    repo, authority = _create_repo(
        tmp_path,
        policy_text=json.dumps(policy),
        schema_text=json.dumps(schema),
    )

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, authority, authority, authority)

    assert caught.value.code == "invalid_policy_schema"


def test_candidate_policy_self_relaxation_does_not_change_authority(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    relaxed = _fixture_policy()
    relaxed["forbidden_module_names"] = ["never_matches"]
    _write(repo, "architecture-policy.json", json.dumps(relaxed, indent=2, sort_keys=True))
    _write(repo, "app/runs/utils.py", "VALUE = True\n")
    head = _commit(repo, "attempt candidate policy relaxation")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "generic_module_name" in _codes(evaluation)
    assert evaluation.policy["owner"] == "platform-architecture"


def _root_inventory_repair_policy(*, added_path: str) -> dict[str, Any]:
    policy = _fixture_policy()
    policy["approved_root_modules"] = sorted(
        [*policy["approved_root_modules"], added_path]
    )
    return policy


def _broken_root_inventory_authority(repo: Path, *, stale_exception: bool = False) -> str:
    _write(repo, "app/new_root_service.py", "VALUE = True\n")
    if stale_exception:
        _write(repo, ".architecture-governance-exception.json", "{}\n")
    return _commit(repo, "introduce unregistered root module")


def test_invalid_authority_root_inventory_can_be_repaired_exactly(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _authority = governance_repo
    broken_authority = _broken_root_inventory_authority(repo)
    repaired = _root_inventory_repair_policy(added_path="app/new_root_service.py")
    _write(repo, POLICY_PATH.name, json.dumps(repaired, indent=2, sort_keys=True) + "\n")
    head = _commit(repo, "repair exact root inventory")

    evaluation = _evaluate(repo, broken_authority, broken_authority, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


def test_root_inventory_repair_may_delete_stale_exception(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _authority = governance_repo
    broken_authority = _broken_root_inventory_authority(repo, stale_exception=True)
    repaired = _root_inventory_repair_policy(added_path="app/new_root_service.py")
    _write(repo, POLICY_PATH.name, json.dumps(repaired, indent=2, sort_keys=True) + "\n")
    (repo / ".architecture-governance-exception.json").unlink()
    head = _commit(repo, "repair inventory and remove stale exception")

    assert _evaluate(repo, broken_authority, broken_authority, head).status == "pass"


def test_root_inventory_repair_rejects_older_authority_than_broken_base(
    governance_repo: tuple[Path, str],
) -> None:
    repo, original_authority = governance_repo
    broken_base = _broken_root_inventory_authority(repo)
    repaired = _root_inventory_repair_policy(added_path="app/new_root_service.py")
    _write(repo, POLICY_PATH.name, json.dumps(repaired, indent=2, sort_keys=True) + "\n")
    head = _commit(repo, "attempt repair from stale authority")

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, original_authority, broken_base, head)

    assert caught.value.code == "invalid_policy_repair"


@pytest.mark.parametrize(
    ("extra_path", "mutate_policy", "keep_exception"),
    [
        ("README.md", False, False),
        (None, True, False),
        (None, False, True),
    ],
)
def test_root_inventory_repair_rejects_broader_candidate_changes(
    governance_repo: tuple[Path, str],
    extra_path: str | None,
    mutate_policy: bool,
    keep_exception: bool,
) -> None:
    repo, _authority = governance_repo
    broken_authority = _broken_root_inventory_authority(
        repo,
        stale_exception=keep_exception,
    )
    repaired = _root_inventory_repair_policy(added_path="app/new_root_service.py")
    if mutate_policy:
        repaired["owner"] = "candidate-owner"
    _write(repo, POLICY_PATH.name, json.dumps(repaired, indent=2, sort_keys=True) + "\n")
    if extra_path is not None:
        _write(repo, extra_path, "candidate change\n")
    head = _commit(repo, "attempt broad inventory repair")

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, broken_authority, broken_authority, head)

    assert caught.value.code == "invalid_policy_repair"


def test_root_inventory_repair_rejects_nonexistent_approved_module(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _authority = governance_repo
    broken_authority = _broken_root_inventory_authority(repo)
    repaired = _root_inventory_repair_policy(added_path="app/new_root_service.py")
    repaired["approved_root_modules"].append("app/not_in_git.py")
    repaired["approved_root_modules"].sort()
    _write(repo, POLICY_PATH.name, json.dumps(repaired, indent=2, sort_keys=True) + "\n")
    head = _commit(repo, "attempt over-approved inventory repair")

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, broken_authority, broken_authority, head)

    assert caught.value.code == "invalid_policy_repair"


def test_new_cross_domain_internal_import_is_non_exemptible(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/skills/application/publish.py",
        "from app.runs.domain import attempt\n",
    )
    head = _commit(repo, "cross-domain internal import")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(item for item in evaluation.findings if item.code == "cross_domain_internal_import")
    assert finding.exemptible is False
    assert finding.details == {"target": "app.runs.domain"}


def test_mixed_public_and_internal_import_does_not_hide_internal_edge(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/skills/application/publish.py",
        "from app.runs import api, secret_internal\n",
    )
    head = _commit(repo, "mixed cross-domain imports")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(item for item in evaluation.findings if item.code == "cross_domain_internal_import")
    assert finding.details == {"target": "app.runs.secret_internal"}


@pytest.mark.parametrize(
    "source",
    [
        'import importlib\nvalue = importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\nvalue = importlib.import_module(name="app.runs.domain.secret")\n',
        'import importlib as loader\nvalue = loader.import_module("app.runs.domain.secret")\n',
        'import importlib.util\nvalue = importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\ndef invoke():\n    return importlib.import_module("app.runs.domain.secret")\n',
        'from importlib import import_module\nvalue = import_module("app.runs.domain.secret")\n',
        'from importlib import import_module\ndef invoke():\n    return import_module("app.runs.domain.secret")\n',
        'from importlib import import_module as loader\nvalue = loader("app.runs.domain.secret")\n',
        'import importlib\nvalue = importlib.import_module(".domain.secret", package="app.runs")\n',
        'import importlib\nvalue = importlib.import_module("app.runs.domain.secret")\nimportlib = object()\n',
        'importlib = object()\nimport importlib\nvalue = importlib.import_module("app.runs.domain.secret")\n',
        'value = __import__("app.runs.domain.secret")\n',
        'value = __import__(name="app.runs.domain.secret")\n',
        'value = __import__("app.runs.domain.secret", level=0)\n',
        'value = __import__("app.runs.domain.secret", level=False)\n',
        'value = __import__("app.runs.domain.secret")\ndef __import__(name):\n    return name\n',
        'def invoke():\n    global importlib\n    import importlib\n    return importlib.import_module("app.runs.domain.secret")\n',
        'def outer():\n    importlib = object()\n    def invoke():\n        nonlocal importlib\n        import importlib\n        return importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\ndef invoke(importlib=importlib.import_module("app.runs.domain.secret")):\n    return importlib\n',
        'import importlib\ninvoke = lambda importlib=importlib.import_module("app.runs.domain.secret"): importlib\n',
        'import importlib\n@importlib.import_module("app.runs.domain.secret")\ndef invoke(importlib):\n    return importlib\n',
        'import importlib\nvalues = [item for importlib in importlib.import_module("app.runs.domain.secret")]\n',
        'import importlib\ndef invoke(importlib=(lambda: importlib.import_module("app.runs.domain.secret"))()):\n    return importlib\n',
        'import importlib\n@(lambda: importlib.import_module("app.runs.domain.secret"))()\ndef invoke(importlib):\n    return importlib\n',
        'import importlib\nvalues = [item for importlib in (lambda: importlib.import_module("app.runs.domain.secret"))()]\n',
        'import importlib\nimportlib: object\nvalue = importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\nclass Holder:\n    importlib: object\n    value = importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\ndef importlib(value=importlib.import_module("app.runs.domain.secret")):\n    return value\n',
        'import importlib\ndef importlib() -> importlib.import_module("app.runs.domain.secret"):\n    pass\n',
        'import importlib\nclass importlib(importlib.import_module("app.runs.domain.secret")):\n    pass\n',
        'import importlib\nclass importlib:\n    value = importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\nclass importlib:\n    value = (lambda: importlib.import_module("app.runs.domain.secret"))()\n',
        'import importlib\nclass importlib:\n    callback = lambda value=importlib.import_module("app.runs.domain.secret"): value\n',
        'import importlib\nclass importlib:\n    values = (item for item in importlib.import_module("app.runs.domain.secret"))\n',
        'import importlib\nclass importlib:\n    values = [importlib.import_module("app.runs.domain.secret") for _ in [0]]\n',
        'import types as loader, importlib as loader\nvalue = loader.import_module("app.runs.domain.secret")\n',
        'import importlib\nimportlib = importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\nvalue = (importlib := importlib.import_module("app.runs.domain.secret"))\n',
        'import importlib\nimportlib: object = importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\nimportlib += importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\nfor importlib in importlib.import_module("app.runs.domain.secret"):\n    pass\n',
    ],
)
def test_literal_dynamic_imports_use_existing_dependency_rules(
    governance_repo: tuple[Path, str], source: str
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/skills/application/publish.py", source)
    head = _commit(repo, "literal dynamic cross-domain import")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(item for item in evaluation.findings if item.code == "cross_domain_internal_import")
    assert finding.exemptible is False
    assert finding.details == {"target": "app.runs.domain.secret"}


@pytest.mark.parametrize(
    "source",
    [
        'def invoke(__import__):\n    return __import__("app.runs.domain.secret")\n',
        'import importlib\ndef invoke(importlib):\n    return importlib.import_module("app.runs.domain.secret")\n',
        'from importlib import import_module\ndef invoke(import_module):\n    return import_module("app.runs.domain.secret")\n',
        'import importlib\nimportlib = object()\nvalue = importlib.import_module("app.runs.domain.secret")\n',
        'from importlib import import_module\ndef invoke():\n    import_module = lambda name: name\n    return import_module("app.runs.domain.secret")\n',
        'import importlib\ndef invoke():\n    global importlib\n    importlib = object()\n    return importlib.import_module("app.runs.domain.secret")\n',
        'def outer():\n    import importlib\n    def invoke():\n        nonlocal importlib\n        importlib = object()\n        return importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\ndef invoke(value=(lambda importlib: importlib.import_module("app.runs.domain.secret"))(object())):\n    return value\n',
        'import importlib\nclass FakeLoader:\n    def import_module(self, name):\n        return name\nvalues = [(importlib := FakeLoader()) for _ in [0]]\nvalue = importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\nimportlib: object = object()\nvalue = importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\ndef invoke():\n    importlib: object\n    return importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\ndef importlib():\n    return importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\nclass importlib:\n    def invoke(self):\n        return importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\nclass importlib:\n    callback = lambda: importlib.import_module("app.runs.domain.secret")\n',
        'import importlib\nclass importlib:\n    values = (importlib.import_module("app.runs.domain.secret") for _ in [0])\n',
        'import importlib as loader, types as loader\nvalue = loader.import_module("app.runs.domain.secret")\n',
        'class FakeLoader:\n    def import_module(self, name):\n        return name\nimport importlib\nimportlib: importlib.import_module("app.runs.domain.secret") = FakeLoader()\n',
    ],
)
def test_shadowed_dynamic_import_names_do_not_create_edges(
    governance_repo: tuple[Path, str], source: str
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/skills/application/publish.py", source)
    head = _commit(repo, "shadowed dynamic import name")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


@pytest.mark.parametrize(
    "source",
    [
        'import importlib\nvalue = importlib.import_module(".domain.policy", package="app.runs")\n',
        'import importlib\npackage = "app.runs"\nvalue = importlib.import_module(".domain.policy", package=package)\n',
        'value = __import__("domain.policy", globals(), locals(), [], 1)\n',
        'value = __import__("app.runs.domain.policy", level=True)\n',
    ],
)
def test_unresolved_relative_dynamic_imports_do_not_create_false_edges(
    governance_repo: tuple[Path, str], source: str
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/runs/application/publish.py", source)
    head = _commit(repo, "relative dynamic import")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


def test_computed_dynamic_import_target_does_not_create_a_static_edge(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/skills/application/publish.py",
        'import importlib\nmodule_name = "app.runs.domain.secret"\nvalue = importlib.import_module(module_name)\n',
    )
    head = _commit(repo, "computed dynamic import")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


def test_rename_rechecks_edges_against_the_new_source_context(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/runs/application/publish.py", "from app.skills import api\n")
    base = _commit(repo, "allowed source context")
    destination = repo / "app" / "skills" / "domain" / "publish.py"
    destination.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "mv", "app/runs/application/publish.py", "app/skills/domain/publish.py")
    head = _commit(repo, "move changes dependency direction")

    evaluation = _evaluate(repo, authority, base, head)

    assert "layer_dependency_forbidden" in _codes(evaluation)


def test_layer_inversion_is_rejected(governance_repo: tuple[Path, str]) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/runs/domain/attempt.py",
        "from app.runs.infrastructure import postgres\n",
    )
    head = _commit(repo, "domain imports infrastructure")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "layer_dependency_forbidden" in _codes(evaluation)


def test_canonical_layer_cannot_import_same_context_legacy_module(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/runs/legacy.py", "LEGACY = True\n")
    base = _commit(repo, "legacy same-context module")
    _write(repo, "app/runs/domain/attempt.py", "from app.runs import legacy\n")
    head = _commit(repo, "canonical layer imports legacy module")

    evaluation = _evaluate(repo, authority, base, head)

    finding = next(item for item in evaluation.findings if item.code == "layer_dependency_forbidden")
    assert finding.exemptible is False
    assert finding.details == {"target": "app.runs.legacy"}


def test_platform_cannot_import_legacy_app_root_module(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/platform/postgres/client.py", "import app.auth\n")
    head = _commit(repo, "platform imports legacy root module")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(item for item in evaluation.findings if item.code == "platform_product_import")
    assert finding.exemptible is False
    assert finding.details == {"target": "app.auth"}


def test_domain_third_party_dependency_is_rejected(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/runs/domain/attempt.py", "import fastapi\n")
    head = _commit(repo, "domain imports framework")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "layer_external_dependency_forbidden" in _codes(evaluation)


def test_legacy_domain_file_cannot_add_same_context_infrastructure_import(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/runs/legacy.py", "BASELINE = True\n")
    base = _commit(repo, "legacy domain file")
    _write(repo, "app/runs/legacy.py", "from app.runs.infrastructure import postgres\n")
    head = _commit(repo, "legacy file imports infrastructure")

    evaluation = _evaluate(repo, authority, base, head)

    assert "layer_dependency_forbidden" in _codes(evaluation)


def test_legacy_domain_file_cannot_add_third_party_dependency(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/runs/legacy.py", "BASELINE = True\n")
    base = _commit(repo, "legacy domain file")
    _write(repo, "app/runs/legacy.py", "import malicious_pkg\n")
    head = _commit(repo, "legacy file imports third party")

    evaluation = _evaluate(repo, authority, base, head)

    assert "layer_external_dependency_forbidden" in _codes(evaluation)


def test_legacy_app_root_file_cannot_add_third_party_dependency(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/auth.py", "BASELINE = True\nimport requests\n")
    head = _commit(repo, "root module imports third party")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "layer_external_dependency_forbidden" in _codes(evaluation)


def test_transitional_persistence_package_is_frozen_to_new_modules(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/persistence/postgres_adapter.py", "VALUE = True\n")
    head = _commit(repo, "extend transitional persistence package")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "unapproved_app_package" in _codes(evaluation)


@pytest.mark.parametrize("boundary", ["api", "events"])
def test_new_context_boundary_modules_are_layer_exempt(
    governance_repo: tuple[Path, str], boundary: str
) -> None:
    repo, authority = governance_repo
    _write(repo, f"app/execution/{boundary}.py", "VALUE = True\n")
    head = _commit(repo, f"add execution {boundary} boundary")

    evaluation = _evaluate(repo, authority, authority, head)

    assert _codes(evaluation) == set()
    assert evaluation.status == "pass"


def test_domain_boundary_cannot_import_concrete_infrastructure(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/runs/api.py", "from app.runs.infrastructure import postgres\n")
    head = _commit(repo, "api imports infrastructure")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "layer_dependency_forbidden" in _codes(evaluation)


def test_arbitrary_kernel_module_is_not_a_public_surface(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/runs/domain/attempt.py", "from app.kernel import secret_policy\n")
    head = _commit(repo, "arbitrary kernel import")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "kernel_public_surface_forbidden" in _codes(evaluation)


def test_kernel_allowlist_does_not_authorize_private_descendants(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    policy = _fixture_policy()
    policy["public_kernel_modules"] = sorted(
        [*policy["public_kernel_modules"], "identity"]
    )
    _write(repo, "architecture-policy.json", json.dumps(policy, indent=2))
    policy_head = _commit(repo, "authority with kernel public module")
    _write(
        repo,
        "app/runs/domain/attempt.py",
        "from app.kernel.identity.private import secret\n",
    )
    head = _commit(repo, "private kernel descendant")

    evaluation = _evaluate(repo, policy_head, policy_head, head)

    assert "kernel_public_surface_forbidden" in _codes(evaluation)


def test_kernel_from_import_resolves_private_descendant_modules(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _authority = governance_repo
    policy = _fixture_policy()
    policy["public_kernel_modules"] = sorted(
        [*policy["public_kernel_modules"], "identity"]
    )
    _write(repo, "architecture-policy.json", json.dumps(policy, indent=2))
    _write(repo, "app/kernel/identity/__init__.py", "class Principal:\n    pass\n")
    _write(repo, "app/kernel/identity/private.py", "SECRET = True\n")
    authority = _commit(repo, "authority with private kernel descendant")
    _write(
        repo,
        "app/runs/domain/attempt.py",
        "from app.kernel.identity import Principal, private\n",
    )
    head = _commit(repo, "import private kernel descendant by alias")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "kernel_public_surface_forbidden" in _codes(evaluation)


def test_kernel_from_import_allows_symbols_from_a_public_module(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _authority = governance_repo
    policy = _fixture_policy()
    policy["public_kernel_modules"] = sorted(
        [*policy["public_kernel_modules"], "identity"]
    )
    _write(repo, "architecture-policy.json", json.dumps(policy, indent=2))
    _write(repo, "app/kernel/identity.py", "class Principal:\n    pass\n")
    authority = _commit(repo, "authority with public kernel module")
    _write(
        repo,
        "app/runs/domain/attempt.py",
        "from app.kernel.identity import Principal\n",
    )
    head = _commit(repo, "import public kernel symbol")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "kernel_public_surface_forbidden" not in _codes(evaluation)


@pytest.mark.parametrize(
    ("source_path", "source", "expected"),
    [
        ("app/kernel/identity.py", "from app.runs import api\n", "kernel_product_import"),
        ("app/platform/postgres/client.py", "from app.files import api\n", "platform_product_import"),
        (
            "app/compat/legacy.py",
            "from app.runs.infrastructure import postgres\n",
            "compatibility_import_forbidden",
        ),
    ],
)
def test_leaf_and_compatibility_import_rules(
    governance_repo: tuple[Path, str], source_path: str, source: str, expected: str
) -> None:
    repo, authority = governance_repo
    _write(repo, source_path, source)
    head = _commit(repo, expected)

    evaluation = _evaluate(repo, authority, authority, head)

    assert expected in _codes(evaluation)


def test_allowed_migration_edges_pass(governance_repo: tuple[Path, str]) -> None:
    repo, authority = governance_repo
    _write(repo, "app/auth.py", "from app.identity import api\n")
    _write(
        repo,
        "app/runs/application/admit.py",
        "from app.runs.domain import attempt\nfrom app.skills import api\n",
    )
    head = _commit(repo, "allowed public migration edges")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


def test_exact_legacy_migration_bridge_moves_symbols_as_identity_aliases(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_agent_profile_bridge(repo)
    head = _commit(repo, "activate exact Agent Profile persistence bridge")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


def test_exact_context_memory_migration_bridge_moves_symbols_as_identity_aliases(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_context_memory_bridge(repo)
    head = _commit(repo, "activate exact Context Memory persistence bridge")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


def test_exact_memory_redaction_kernel_bridge_uses_existing_contract(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_memory_redaction_bridge(repo)
    head = _commit(repo, "activate memory redaction Kernel bridge")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


@pytest.mark.parametrize(
    "drift",
    [
        "annotation_only",
        "conditional_only_binding",
        "conditional_duplicate_binding",
        "duplicate_binding",
        "computed_dynamic_import",
        "builtins_mapping_dynamic_import",
        "imported_assignment",
        "transitive_imported_assignment",
    ],
)
def test_migration_bridge_target_requires_runtime_local_ownership(
    governance_repo: tuple[Path, str], drift: str
) -> None:
    repo, authority = governance_repo
    bridge = _migration_bridge(
        source_path="app/memory_redaction.py",
        target_module="app.kernel.memory_redaction",
    )
    _activate_memory_redaction_bridge(repo)
    target_path = repo / "app/kernel/memory_redaction.py"
    source = target_path.read_text(encoding="utf-8")
    symbol = bridge["symbols"][0]
    if drift == "annotation_only":
        source = source.replace(f"{symbol} = 1", f"{symbol}: object")
    elif drift == "conditional_only_binding":
        source = source.replace(f"{symbol} = 1", f"if False:\n    {symbol} = 1")
    elif drift == "conditional_duplicate_binding":
        source += f"if True:\n    {symbol} = 2\n"
    elif drift == "duplicate_binding":
        source += f"{symbol} = 2\n"
    elif drift == "computed_dynamic_import":
        source += "module_name = 'app.' + 'memory_redaction'\nLOADED = __import__(module_name)\n"
    elif drift == "builtins_mapping_dynamic_import":
        source += (
            "module_name = 'app.' + 'memory_redaction'\n"
            'LOADED = __builtins__["__import__"](module_name)\n'
        )
    elif drift == "transitive_imported_assignment":
        source = "from re import IGNORECASE as imported_value\n_value = imported_value\n" + (
            source.replace(f"{symbol} = 1", f"{symbol} = _value")
        )
    else:
        source = "from re import IGNORECASE as value\n" + source.replace(
            f"{symbol} = 1", f"{symbol} = value"
        )
    target_path.write_text(source, encoding="utf-8")
    head = _commit(repo, f"reject bridge target {drift}")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "migration_bridge_target_contract" in _codes(evaluation)


@pytest.mark.parametrize("alias_form", ["annotated", "chained"])
def test_migration_bridge_rejects_non_plain_identity_aliases(
    governance_repo: tuple[Path, str], alias_form: str
) -> None:
    repo, authority = governance_repo
    bridge = _migration_bridge(
        source_path="app/memory_redaction.py",
        target_module="app.kernel.memory_redaction",
    )
    _activate_memory_redaction_bridge(repo)
    source_path = repo / bridge["source_path"]
    source = source_path.read_text(encoding="utf-8")
    symbol = bridge["symbols"][0]
    exact = f"{symbol} = {bridge['module_alias']}.{symbol}"
    replacement = (
        f"{symbol}: object = {bridge['module_alias']}.{symbol}"
        if alias_form == "annotated"
        else f"{symbol} = extra_binding = {bridge['module_alias']}.{symbol}"
    )
    source_path.write_text(source.replace(exact, replacement), encoding="utf-8")
    head = _commit(repo, f"reject bridge {alias_form} alias")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "migration_bridge_symbol_contract" in _codes(evaluation)


def test_exact_conversation_migration_bridges_move_symbols_as_identity_aliases(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_conversation_bridges(repo)
    head = _commit(repo, "activate exact Conversation persistence bridges")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


def test_exact_runs_migration_bridge_moves_symbols_as_identity_aliases(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_runs_bridge(repo)
    head = _commit(repo, "activate exact Runs persistence bridge")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


def test_exact_repository_authorization_error_bridge_moves_identity_alias(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_repository_authorization_error_bridge(repo)
    head = _commit(repo, "activate exact repository authorization error bridge")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


def test_multiple_declared_bridges_can_share_one_legacy_source(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_all_migration_bridges(repo)
    head = _commit(repo, "activate all declared persistence bridges")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


def test_multiple_declared_bridges_do_not_allow_new_source_logic(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_all_migration_bridges(repo)
    source_path = repo / "app/repositories.py"
    source_path.write_text(
        source_path.read_text(encoding="utf-8")
        + "\ndef newly_owned_logic():\n    return True\n",
        encoding="utf-8",
    )
    head = _commit(repo, "reject logic beside multiple declared bridges")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "migration_bridge_source_logic"
        and item.path == "app/repositories.py"
    )
    assert finding.exemptible is False


def test_bridge_activation_rejects_undeclared_baseline_node_deletion(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_all_migration_bridges(repo)
    source_path = repo / "app/repositories.py"
    source = source_path.read_text(encoding="utf-8")
    unrelated_node = 'DEFAULT_RUN_EXECUTOR_TYPES = {"claude-agent-worker"}\n'
    assert source.count(unrelated_node) == 1
    source_path.write_text(source.replace(unrelated_node, ""), encoding="utf-8")
    head = _commit(repo, "reject undeclared baseline deletion during bridge activation")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "migration_bridge_source_logic"
        and item.path == "app/repositories.py"
    )
    assert finding.exemptible is False
    assert finding.details["undeclared_removed_nodes"] == 1


@pytest.mark.parametrize("hidden_binding", ["starred", "named_expression"])
def test_bridge_activation_rejects_hidden_undeclared_baseline_bindings(
    governance_repo: tuple[Path, str],
    hidden_binding: str,
) -> None:
    repo, authority = governance_repo
    bridges = [
        bridge
        for bridge in _fixture_policy()["migration_bridges"]
        if bridge["source_path"] == "app/repositories.py"
    ]
    first_symbol = bridges[0]["symbols"][0]
    second_symbol = bridges[1]["symbols"][0]
    mixed_node = (
        f"({first_symbol}, *{second_symbol}) = values"
        if hidden_binding == "starred"
        else f"{first_symbol} = (UNDECLARED := value)"
    )
    source_path = repo / "app/repositories.py"
    source_path.write_text(
        source_path.read_text(encoding="utf-8") + f"\n{mixed_node}\n",
        encoding="utf-8",
    )
    base = _commit(repo, f"accept {hidden_binding} baseline binding")
    _activate_all_migration_bridges(repo)
    head = _commit(repo, f"reject hidden {hidden_binding} deletion")

    evaluation = _evaluate(repo, authority, base, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "migration_bridge_source_logic"
        and item.path == "app/repositories.py"
    )
    assert finding.exemptible is False
    assert finding.details["undeclared_removed_nodes"] == 1


@pytest.mark.parametrize("declaration_kind", ["class", "function"])
@pytest.mark.parametrize(
    "module_state_form",
    [
        "global_statement",
        "globals_subscript",
        "builtins_globals_subscript",
        "current_module_setattr",
        "builtins_current_module_setattr",
    ],
)
def test_bridge_activation_rejects_declaration_with_dynamic_module_state(
    governance_repo: tuple[Path, str],
    declaration_kind: str,
    module_state_form: str,
) -> None:
    repo, authority = governance_repo
    target_module = (
        "app.platform.postgres.errors"
        if declaration_kind == "class"
        else "app.agent_apps.infrastructure.postgres"
    )
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module=target_module,
    )
    symbol = bridge["symbols"][0]
    module_state_body = {
        "global_statement": "global UNDECLARED\n    UNDECLARED = value",
        "globals_subscript": 'globals()["UNDECLARED"] = value',
        "builtins_globals_subscript": (
            'builtins.globals()["UNDECLARED"] = value'
        ),
        "current_module_setattr": (
            'setattr(sys.modules[__name__], "UNDECLARED", value)'
        ),
        "builtins_current_module_setattr": (
            'builtins.setattr(sys.modules[__name__], "UNDECLARED", value)'
        ),
    }[module_state_form]
    if declaration_kind == "class":
        plain_declaration = f"class {symbol}:\n    pass"
        module_state_declaration = f"class {symbol}:\n    {module_state_body}"
    else:
        plain_declaration = (
            f"async def {symbol}():\n"
            f"    marker = {symbol!r}\n"
            "    return marker"
        )
        module_state_declaration = (
            f"async def {symbol}():\n"
            f"    {module_state_body}\n"
            "    return UNDECLARED"
        )
    source_path = repo / bridge["source_path"]
    source = source_path.read_text(encoding="utf-8")
    assert source.count(plain_declaration) == 1
    source_path.write_text(
        source.replace(plain_declaration, module_state_declaration),
        encoding="utf-8",
    )
    base = _commit(repo, f"accept {declaration_kind} with {module_state_form}")
    _activate_all_migration_bridges(repo)
    head = _commit(repo, f"reject {declaration_kind} module-state move")

    evaluation = _evaluate(repo, authority, base, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "migration_bridge_source_logic"
        and item.path == bridge["source_path"]
    )
    assert finding.exemptible is False
    assert finding.details["undeclared_removed_nodes"] == 1


def test_bridge_activation_allows_unrelated_globals_method(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.agent_apps.infrastructure.postgres",
    )
    symbol = bridge["symbols"][0]
    plain_declaration = (
        f"async def {symbol}():\n"
        f"    marker = {symbol!r}\n"
        "    return marker"
    )
    method_declaration = (
        f"async def {symbol}():\n"
        "    return registry.globals()"
    )
    source_path = repo / bridge["source_path"]
    source = source_path.read_text(encoding="utf-8")
    assert source.count(plain_declaration) == 1
    source_path.write_text(
        source.replace(plain_declaration, method_declaration),
        encoding="utf-8",
    )
    base = _commit(repo, "accept unrelated globals method")
    _activate_all_migration_bridges(repo)
    head = _commit(repo, "activate bridge with unrelated globals method")

    evaluation = _evaluate(repo, authority, base, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


def test_bridge_activation_rejects_deleting_non_declaration_node(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    bridge = next(
        bridge
        for bridge in _fixture_policy()["migration_bridges"]
        if bridge["source_path"] == "app/repositories.py"
    )
    deletion = f"del {bridge['symbols'][0]}\n"
    source_path = repo / "app/repositories.py"
    source_path.write_text(
        source_path.read_text(encoding="utf-8") + f"\n{deletion}",
        encoding="utf-8",
    )
    base = _commit(repo, "accept baseline deletion node")
    _activate_all_migration_bridges(repo)
    head = _commit(repo, "reject removal of non-declaration node")

    evaluation = _evaluate(repo, authority, base, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "migration_bridge_source_logic"
        and item.path == "app/repositories.py"
    )
    assert finding.exemptible is False
    assert finding.details["undeclared_removed_nodes"] == 1


def test_exact_legacy_api_cutover_rewrites_only_declared_symbols(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_legacy_api_cutover(repo)
    head = _commit(repo, "cut over legacy policy to public Runs API")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


def test_exact_legacy_api_cutover_composes_with_simultaneous_bridge_activation(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_all_migration_bridges(repo)
    _activate_legacy_api_cutover(repo)
    head = _commit(repo, "activate persistence bridges and cut over public API")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


def test_exact_legacy_api_cutover_composes_with_active_migration_bridges(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_all_migration_bridges(repo)
    base = _commit(repo, "activate persistence bridges")
    _activate_legacy_api_cutover(repo)
    head = _commit(repo, "cut over frozen bridge source to public API")

    evaluation = _evaluate(repo, authority, base, head)

    assert evaluation.status == "pass"
    assert evaluation.findings == ()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("\ndef newly_owned_logic():\n    return True\n", "legacy_api_cutover_source_logic"),
        ("\nimport importlib\n", "legacy_api_cutover_contract"),
        ("\nOLD = TERMINAL_RUN_STATUSES\n", "legacy_api_cutover_contract"),
        ("\nUNDECLARED = runs_api.private_policy\n", "legacy_api_cutover_contract"),
    ],
)
def test_legacy_api_cutover_rejects_extra_logic_dynamic_imports_and_references(
    governance_repo: tuple[Path, str], mutation: str, expected_code: str
) -> None:
    repo, authority = governance_repo
    _activate_legacy_api_cutover(repo)
    source_path = repo / "app/repositories.py"
    source_path.write_text(
        source_path.read_text(encoding="utf-8") + mutation,
        encoding="utf-8",
    )
    head = _commit(repo, "reject expanded cutover")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(item for item in evaluation.findings if item.code == expected_code)
    assert finding.exemptible is False


def test_legacy_api_cutover_rejects_missing_public_target(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_legacy_api_cutover(repo)
    (repo / "app/runs/api.py").unlink()
    head = _commit(repo, "remove cutover target")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(
        item for item in evaluation.findings if item.code == "legacy_api_cutover_target_contract"
    )
    assert finding.exemptible is False


def test_legacy_api_cutover_public_target_must_be_identity_only(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_legacy_api_cutover(repo)
    target = repo / "app/runs/api.py"
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\n\ndef progress_for_requested_status(value):\n    return 'replacement'\n",
        encoding="utf-8",
    )
    head = _commit(repo, "reject public API implementation during cutover")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(
        item for item in evaluation.findings if item.code == "legacy_api_cutover_target_contract"
    )
    assert finding.exemptible is False


def test_legacy_api_cutover_requires_the_canonical_owner_module(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_legacy_api_cutover(repo)
    (repo / "app/runs/domain/terminalization.py").unlink()
    head = _commit(repo, "remove canonical cutover owner")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "legacy_api_cutover_target_contract"
        and item.path == "app/runs/domain/terminalization.py"
    )
    assert finding.exemptible is False


def test_legacy_api_cutover_canonical_symbols_cannot_be_re_exports(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_legacy_api_cutover(repo)
    cutover = _legacy_api_cutover()
    target_symbols = sorted(rewrite["new_symbol"] for rewrite in cutover["rewrites"])
    _write(
        repo,
        "app/runs/domain/terminalization.py",
        "from app.runs.infrastructure.postgres import "
        + ", ".join(f"{name} as {name}" for name in target_symbols)
        + "\n",
    )
    head = _commit(repo, "reject canonical re-export owner")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "legacy_api_cutover_target_contract"
        and item.path == "app/runs/domain/terminalization.py"
    )
    assert finding.exemptible is False


def test_legacy_api_cutover_canonical_symbols_cannot_alias_imported_values(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_legacy_api_cutover(repo)
    canonical = repo / "app/runs/domain/terminalization.py"
    canonical.write_text(
        "from app.runs.infrastructure import postgres as upstream\n\n"
        "TERMINAL_RUN_STATUSES = upstream.TERMINAL_RUN_STATUSES\n\n"
        "class RunTerminalizationProgress:\n    pass\n\n"
        "def progress_for_requested_status(value=None):\n    return value\n",
        encoding="utf-8",
    )
    head = _commit(repo, "reject imported canonical value alias")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "legacy_api_cutover_target_contract"
        and item.path == "app/runs/domain/terminalization.py"
    )
    assert "cannot depend on imported aliases" in finding.details["issues"][0]


def test_legacy_api_cutover_canonical_values_cannot_use_dynamic_imports(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_legacy_api_cutover(repo)
    canonical = repo / "app/runs/domain/terminalization.py"
    canonical.write_text(
        "TERMINAL_RUN_STATUSES = __import__('app.runtime').TERMINAL_RUN_STATUSES\n\n"
        "class RunTerminalizationProgress:\n    pass\n\n"
        "def progress_for_requested_status(value=None):\n    return value\n",
        encoding="utf-8",
    )
    head = _commit(repo, "reject dynamic canonical value")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "legacy_api_cutover_target_contract"
        and item.path == "app/runs/domain/terminalization.py"
    )
    assert any(
        "must be a static value definition" in issue
        for issue in finding.details["issues"]
    )


@pytest.mark.parametrize(
    "target_path",
    ["app/runs/api.py", "app/runs/domain/terminalization.py"],
)
def test_pending_legacy_cutover_targets_cannot_change_without_the_source(
    governance_repo: tuple[Path, str],
    target_path: str,
) -> None:
    repo, authority = governance_repo
    _write(repo, target_path, "UNRELATED = True\n")
    head = _commit(repo, "change pending cutover target alone")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "legacy_api_cutover_target_contract" and item.path == target_path
    )
    assert finding.exemptible is False


def test_authority_rejects_a_consumed_cutover_without_its_canonical_owner(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _authority = governance_repo
    _activate_legacy_api_cutover(repo)
    (repo / "app/runs/domain/terminalization.py").unlink()
    invalid_authority = _commit(repo, "consume cutover without canonical owner")

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, invalid_authority, invalid_authority, invalid_authority)

    assert caught.value.code == "invalid_policy"


def test_consumed_cutover_target_cannot_be_deleted_before_authority_retirement(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _authority = governance_repo
    _activate_legacy_api_cutover(repo)
    consumed_authority = _commit(repo, "consume cutover")
    (repo / "app/runs/domain/terminalization.py").unlink()
    head = _commit(repo, "delete consumed canonical owner")

    evaluation = _evaluate(repo, consumed_authority, consumed_authority, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "legacy_api_cutover_target_contract"
        and item.path == "app/runs/domain/terminalization.py"
    )
    assert finding.exemptible is False


@pytest.mark.parametrize(
    "legacy_import",
    [
        "from app.compat import TERMINAL_RUN_STATUSES\n",
        (
            "from app.compat import RunTerminalizationProgress "
            "as ToolPermissionTerminalizationProgress\n"
        ),
    ],
)
def test_authority_rejects_consumed_cutover_with_legacy_import_bindings(
    governance_repo: tuple[Path, str],
    legacy_import: str,
) -> None:
    repo, _authority = governance_repo
    _activate_legacy_api_cutover(repo)
    source_path = repo / "app/repositories.py"
    source_path.write_text(
        legacy_import + source_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    invalid_authority = _commit(repo, "retain a legacy import binding")

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, invalid_authority, invalid_authority, invalid_authority)

    assert caught.value.code == "invalid_policy"


def test_legacy_api_cutover_rejects_retained_statement_reordering(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_legacy_api_cutover(repo)
    source_path = repo / "app/repositories.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    tree.body[0], tree.body[1] = tree.body[1], tree.body[0]
    source_path.write_text(ast.unparse(tree) + "\n", encoding="utf-8")
    head = _commit(repo, "reject top-level initialization reorder")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "legacy_api_cutover_source_logic" in _codes(evaluation)


def test_authority_rejects_a_multi_binding_legacy_definition(
    governance_repo: tuple[Path, str],
) -> None:
    repo, _authority = governance_repo
    suffix = _fixture_cutover_suffix("app/repositories.py")
    source_path = repo / "app/repositories.py"
    current = source_path.read_text(encoding="utf-8")
    mutated_suffix = suffix.replace(
        "TERMINAL_RUN_STATUSES = 1",
        "TERMINAL_RUN_STATUSES = UNRELATED_STATE = 1",
    )
    assert mutated_suffix != suffix
    source_path.write_text(
        current[: -len(suffix)] + mutated_suffix,
        encoding="utf-8",
    )
    base = _commit(repo, "introduce unsafe combined legacy binding")
    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, base, base, base)

    assert caught.value.code == "invalid_policy"


def test_legacy_api_cutover_rejects_source_deletion(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    (repo / "app/repositories.py").unlink()
    head = _commit(repo, "delete governed cutover source")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "legacy_api_cutover_contract" in _codes(evaluation)


def test_consumed_legacy_api_cutover_requires_authority_retirement_before_more_changes(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_legacy_api_cutover(repo)
    consumed_authority = _commit(repo, "consume cutover")
    source_path = repo / "app/repositories.py"
    source_path.write_text(
        source_path.read_text(encoding="utf-8") + "\nPOST_CUTOVER = True\n",
        encoding="utf-8",
    )
    head = _commit(repo, "attempt source change before retirement")

    evaluation = _evaluate(repo, consumed_authority, consumed_authority, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "legacy_api_cutover_retirement_required"
    )
    assert finding.exemptible is False


@pytest.mark.parametrize(
    "public_module",
    [
        "app.runs.infrastructure.postgres",
        "app.runs.private",
        "app.runs.api.private",
        "app.unknown.api",
    ],
)
def test_authority_rejects_nonpublic_legacy_cutover_targets(
    tmp_path: Path, public_module: str
) -> None:
    policy = _fixture_policy()
    policy["legacy_api_cutovers"][0]["public_module"] = public_module
    repo, authority = _create_repo(tmp_path, policy_text=json.dumps(policy))

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, authority, authority, authority)

    assert caught.value.code == "invalid_policy"


def test_legacy_api_cutover_findings_are_non_exemptible_authority() -> None:
    policy = _fixture_policy()
    expected = {
        "legacy_api_cutover_contract",
        "legacy_api_cutover_retirement_required",
        "legacy_api_cutover_source_logic",
        "legacy_api_cutover_target_contract",
    }

    assert expected <= set(policy["exception_contract"]["non_exemptible_codes"])
    assert expected <= architecture_governance.BUILTIN_NON_EXEMPTIBLE_CODES


def test_retired_runs_legacy_api_cutover_fixture_is_exact() -> None:
    expected = {
        "source_path": "app/repositories.py",
        "public_module": "app.runs.api",
        "canonical_module": "app.runs.domain.terminalization",
        "module_alias": "runs_api",
        "removed_imports": [{"module": "dataclasses", "name": "dataclass"}],
        "rewrites": [
            {
                "old_symbol": "TERMINAL_RUN_STATUSES",
                "new_symbol": "TERMINAL_RUN_STATUSES",
            },
            {
                "old_symbol": "ToolPermissionTerminalizationProgress",
                "new_symbol": "RunTerminalizationProgress",
            },
            {
                "old_symbol": "_terminalization_progress_for_requested_status",
                "new_symbol": "progress_for_requested_status",
            },
        ],
        "owner": "runs",
        "reason": (
            "The frozen global repository may perform one exact hard cut from its "
            "locally owned Run terminalization policy symbols to the public Runs API "
            "without retaining policy aliases or importing Runs internals."
        ),
        "removal_condition": (
            "After the exact Runs policy cutover is merged, remove this consumed "
            "authority entry before any further app/repositories.py source change."
        ),
    }

    assert _retired_runs_legacy_api_cutover() == expected
    assert _legacy_api_cutover() == expected


def test_conversation_migration_bridge_authority_is_exact() -> None:
    agent_history = _migration_bridge(
        source_path="app/agent_conversation_repository.py",
        target_module="app.conversations.infrastructure.postgres",
    )
    session_messages = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.conversations.infrastructure.postgres",
    )

    assert agent_history["module_alias"] == "conversation_persistence"
    assert agent_history["symbols"] == ["list_authorized_agent_conversations"]
    assert session_messages["module_alias"] == "conversation_persistence"
    assert session_messages["symbols"] == [
        "append_message",
        "create_session",
        "ensure_workspace_belongs_to_tenant",
        "get_authorized_lambchat_session",
        "get_authorized_session_projection",
        "get_session_for_action",
        "list_authorized_messages",
        "list_authorized_sessions",
        "list_authorized_user_messages_for_runs",
        "list_session_messages_for_fork",
        "mark_session_deleted",
        "update_session_title",
    ]


def test_context_snapshot_persistence_bridge_authority_is_exact_and_pending() -> None:
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.context.infrastructure.snapshot_postgres",
    )

    assert bridge == {
        "source_path": "app/repositories.py",
        "target_module": "app.context.infrastructure.snapshot_postgres",
        "module_alias": "context_snapshot_persistence",
        "symbols": [
            "CONTEXT_SNAPSHOT_MEMBER_BATCH_LIMIT",
            "_normalize_context_snapshot_member_ids",
            "create_context_snapshot",
            "get_bound_executor_context_snapshot",
            "get_context_snapshot_for_worker",
            "get_latest_authorized_executor_context_snapshot",
            "list_context_share_snapshots_for_target_session",
            "list_context_snapshots",
            "update_run_context_snapshot_ref",
        ],
        "owner": "context",
        "reason": (
            "The frozen global repository may expose these existing immutable "
            "Context snapshot persistence symbols only as exact identity aliases "
            "while their implementation moves to the Context snapshot adapter."
        ),
        "removal_condition": (
            "After the Context snapshot persistence move, migrate supported internal "
            "callers to the Context API, inventory external imports, and remove this "
            "bridge in an authority-only change before deleting the repositories "
            "aliases."
        ),
    }

    target_path = REPO_ROOT / "app/context/infrastructure/snapshot_postgres.py"
    source_tree = ast.parse((REPO_ROOT / bridge["source_path"]).read_text(encoding="utf-8"))

    assert not target_path.exists()
    assert [
        (imported.name, imported.asname)
        for node in source_tree.body
        if isinstance(node, ast.Import)
        for imported in node.names
        if imported.name == bridge["target_module"]
    ] == []
    source_binding_counts = architecture_governance._top_level_local_binding_counts(source_tree)
    assert {symbol: source_binding_counts.get(symbol, 0) for symbol in bridge["symbols"]} == {
        symbol: 1 for symbol in bridge["symbols"]
    }
    assert {
        node.name
        for node in source_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in bridge["symbols"]
    } == set(bridge["symbols"][1:])
    batch_limit_assignments = [
        node
        for node in source_tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "CONTEXT_SNAPSHOT_MEMBER_BATCH_LIMIT"
    ]
    assert len(batch_limit_assignments) == 1
    assert ast.literal_eval(batch_limit_assignments[0].value) == 128


def test_live_context_source_persistence_bridge_is_exact_and_active() -> None:
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.context.infrastructure.sources_postgres",
    )

    assert bridge == {
        "source_path": "app/repositories.py",
        "target_module": "app.context.infrastructure.sources_postgres",
        "module_alias": "context_sources_persistence",
        "symbols": [
            "count_session_context_messages",
            "get_scoped_context_artifact",
            "get_scoped_context_file",
            "list_authorized_context_file_rows",
            "list_scoped_context_messages",
            "list_session_context_artifacts",
            "list_session_context_files",
            "list_session_context_messages",
            "session_has_legacy_run_history",
        ],
        "owner": "context",
        "reason": (
            "The frozen global repository may expose these existing immutable-"
            "snapshot and prior-session Context source-read symbols only as exact "
            "identity aliases while their PostgreSQL implementation moves to the "
            "Context source adapter."
        ),
        "removal_condition": (
            "After the Context source-read persistence move, migrate supported "
            "internal callers to the Context API, inventory external imports, and "
            "remove this bridge in an authority-only change before deleting the "
            "repositories aliases."
        ),
    }

    target_path = REPO_ROOT / "app/context/infrastructure/sources_postgres.py"
    source = (REPO_ROOT / bridge["source_path"]).read_text(encoding="utf-8")
    source_tree = ast.parse(source)

    assert target_path.exists()
    target_tree = ast.parse(target_path.read_text(encoding="utf-8"))
    source_async_functions = {
        node.name for node in source_tree.body if isinstance(node, ast.AsyncFunctionDef)
    }
    target_async_functions = [
        node.name for node in target_tree.body if isinstance(node, ast.AsyncFunctionDef)
    ]

    assert set(bridge["symbols"]).isdisjoint(source_async_functions)
    assert sorted(target_async_functions) == bridge["symbols"]
    target_imports = [
        (imported.name, imported.asname)
        for node in source_tree.body
        if isinstance(node, ast.Import)
        for imported in node.names
        if imported.name == bridge["target_module"]
    ]
    assert target_imports == [(bridge["target_module"], bridge["module_alias"])]

    source_binding_counts = architecture_governance._top_level_local_binding_counts(source_tree)
    assert {symbol: source_binding_counts[symbol] for symbol in bridge["symbols"]} == {
        symbol: 1 for symbol in bridge["symbols"]
    }
    source_aliases = [
        (target.id, node.value.value.id, node.value.attr)
        for node in source_tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance((target := node.targets[0]), ast.Name)
        and target.id in bridge["symbols"]
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
    ]
    assert sorted(source_aliases) == [
        (symbol, bridge["module_alias"], symbol) for symbol in bridge["symbols"]
    ]
    for symbol in bridge["symbols"]:
        assert target_async_functions.count(symbol) == 1

    from app import repositories
    from app.context.infrastructure import sources_postgres

    for symbol in bridge["symbols"]:
        assert getattr(repositories, symbol) is getattr(sources_postgres, symbol)

    program = """
import sys

import app.context.infrastructure.sources_postgres

assert "app.context.retrieval" not in sys.modules
assert "app.repositories" not in sys.modules
"""
    subprocess.run(
        [sys.executable, "-c", program],
        cwd=REPO_ROOT,
        check=True,
    )


def test_context_memory_persistence_bridge_authority_is_exact() -> None:
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.context.infrastructure.postgres",
    )

    assert bridge == {
        "source_path": "app/repositories.py",
        "target_module": "app.context.infrastructure.postgres",
        "module_alias": "memory_persistence",
        "symbols": [
            "MEMORY_RETENTION_CLEANUP_CURSOR_KEY",
            "_default_memory_policy",
            "_list_expired_memory_cleanup_scopes",
            "_memory_policy_from_row",
            "_stored_memory_redaction_mode",
            "_validated_memory_redaction_mode",
            "admin_delete_memory_record",
            "cleanup_expired_memory_records",
            "cleanup_expired_memory_records_across_scopes",
            "create_memory_record",
            "delete_memory_record",
            "get_effective_memory_policy",
            "list_admin_memory_policies",
            "list_admin_memory_records",
            "list_memory_records",
            "list_scoped_context_memory_records",
            "memory_policy_id",
            "set_memory_policy",
        ],
        "owner": "context",
        "reason": (
            "The frozen global repository may expose these existing Memory policy, "
            "record, scoped-read, and retention persistence symbols only as exact "
            "identity aliases while their PostgreSQL implementation moves to the "
            "Context adapter."
        ),
        "removal_condition": (
            "After the Context Memory persistence move, migrate supported internal "
            "callers to the Context API, inventory external imports, and remove this "
            "bridge in an authority-only change before deleting the repositories "
            "aliases."
        ),
    }


def test_context_postgres_import_does_not_initialize_legacy_repository_cycle() -> None:
    program = """
import sys

import app.context.infrastructure.postgres

assert "app.context.retrieval" not in sys.modules
assert "app.repositories" not in sys.modules

import app.context as context

try:
    context.not_a_public_export
except AttributeError:
    pass
else:
    raise AssertionError("unknown Context exports must fail closed")
assert "app.context.retrieval" not in sys.modules

from app.context import retrieval

for name in context.__all__:
    assert getattr(context, name) is getattr(retrieval, name)
"""
    subprocess.run(
        [sys.executable, "-c", program],
        cwd=REPO_ROOT,
        check=True,
    )


def test_live_context_memory_persistence_bridge_is_exact_and_active() -> None:
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.context.infrastructure.postgres",
    )
    target_path = REPO_ROOT / "app/context/infrastructure/postgres.py"
    source = (REPO_ROOT / bridge["source_path"]).read_text(encoding="utf-8")

    assert target_path.exists()
    assert f"import {bridge['target_module']} as {bridge['module_alias']}" in source
    for symbol in bridge["symbols"]:
        assert f"{symbol} = {bridge['module_alias']}.{symbol}" in source

    from app import repositories
    from app.context.infrastructure import postgres as context_memory_persistence

    for symbol in bridge["symbols"]:
        assert getattr(repositories, symbol) is getattr(
            context_memory_persistence, symbol
        )


def test_live_memory_redaction_kernel_bridge_is_exact_and_active() -> None:
    bridge = _migration_bridge(
        source_path="app/memory_redaction.py",
        target_module="app.kernel.memory_redaction",
    )
    source = (REPO_ROOT / bridge["source_path"]).read_text(encoding="utf-8")
    target_path = REPO_ROOT / "app/kernel/memory_redaction.py"

    assert bridge == {
        "source_path": "app/memory_redaction.py",
        "target_module": "app.kernel.memory_redaction",
        "module_alias": "memory_redaction_kernel",
        "symbols": [
            "MEMORY_REDACTION_MODES",
            "MEMORY_REDACTION_MODE_STANDARD",
            "MEMORY_REDACTION_MODE_STRICT",
            "is_sensitive_redaction_key",
            "normalize_memory_redaction_mode",
            "redact_memory_metadata",
            "redact_memory_metadata_value",
            "redact_memory_text",
            "sanitizer_unstable_assignment_suffix_length",
            "sanitizer_unstable_suffix_length",
        ],
        "owner": "kernel",
        "reason": (
            "The shared memory-redaction policy may move from its approved legacy "
            "root into one framework-neutral Kernel module while the legacy import "
            "surface remains exact identity aliases."
        ),
        "removal_condition": (
            "After all supported callers import the Kernel owner directly, remove "
            "this bridge before deleting the legacy facade."
        ),
    }
    assert target_path.exists()
    assert source == (
        f"import {bridge['target_module']} as {bridge['module_alias']}\n\n"
        + "\n".join(
            f"{symbol} = {bridge['module_alias']}.{symbol}"
            for symbol in bridge["symbols"]
        )
        + "\n"
    )

    from app import memory_redaction
    from app.kernel import memory_redaction as kernel_memory_redaction

    for symbol in bridge["symbols"]:
        assert getattr(memory_redaction, symbol) is getattr(
            kernel_memory_redaction, symbol
        )


def test_public_kernel_migration_bridge_requires_kernel_allowlist(
    tmp_path: Path,
) -> None:
    policy = _fixture_policy()
    policy["public_kernel_modules"] = []
    repo, authority = _create_repo(tmp_path, policy_text=json.dumps(policy))

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, authority, authority, authority)

    assert caught.value.code == "invalid_policy"


def test_persistence_limits_platform_bridge_authority_is_exact() -> None:
    bridge = _migration_bridge(
        source_path="app/persistence_limits.py",
        target_module="app.platform.postgres.limits",
    )

    assert bridge == {
        "source_path": "app/persistence_limits.py",
        "target_module": "app.platform.postgres.limits",
        "module_alias": "postgres_limits",
        "symbols": [
            "ARTIFACT_MANIFEST_MAX_BYTES",
            "AUDIT_PAYLOAD_MAX_BYTES",
            "CONTEXT_SNAPSHOT_PAYLOAD_MAX_BYTES",
            "MESSAGE_CONTENT_MAX_BYTES",
            "MESSAGE_METADATA_MAX_BYTES",
            "PersistenceSizeLimitError",
            "RUN_EVENT_MESSAGE_MAX_BYTES",
            "RUN_EVENT_PAYLOAD_MAX_BYTES",
            "RUN_INPUT_MAX_BYTES",
            "RUN_RESULT_MAX_BYTES",
            "RUN_STEP_PAYLOAD_MAX_BYTES",
            "compact_json_dumps",
            "ensure_json_size",
            "ensure_text_size",
            "json_size_bytes",
        ],
        "owner": "platform-architecture",
        "reason": (
            "The root-level persistence-limit module may preserve its existing "
            "byte-bound symbols only as exact identity aliases while the technical "
            "PostgreSQL implementation moves to the platform adapter."
        ),
        "removal_condition": (
            "After the PostgreSQL limit move, inventory all supported imports, "
            "migrate internal callers to the platform boundary, and remove this "
            "bridge in an authority-only change before deleting the root-level aliases."
        ),
    }


def test_runs_persistence_bridge_authority_is_exact() -> None:
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.runs.infrastructure.postgres",
    )

    assert bridge == {
        "source_path": "app/repositories.py",
        "target_module": "app.runs.infrastructure.postgres",
        "module_alias": "run_persistence",
        "symbols": [
            "_stage_run_tool_permission_terminalization",
            "acquire_user_active_run_admission_lock",
            "count_active_runs_for_user",
            "enforce_user_active_run_admission",
            "enforce_user_active_run_admission_under_lock",
            "get_active_resume_for_source_run",
            "get_active_retry_for_source_run",
            "get_run",
            "get_run_identity",
        ],
        "owner": "runs",
        "reason": (
            "The frozen global repository may expose these existing run identity, "
            "admission-lock, active-child, and terminal-intent persistence primitives "
            "only as exact identity aliases while their PostgreSQL implementation "
            "moves to the Runs adapter."
        ),
        "removal_condition": (
            "After the Runs persistence move, migrate supported internal callers to "
            "the Runs API, inventory external imports, and remove this bridge in an "
            "authority-only change before deleting the repositories aliases."
        ),
    }


def test_runs_lifecycle_orchestration_is_not_authorized_as_persistence() -> None:
    policy = _fixture_policy()
    repository_bridges = [
        item
        for item in policy["migration_bridges"]
        if item["source_path"] == "app/repositories.py"
    ]

    assert "app.runs.infrastructure.lifecycle" not in {
        item["target_module"] for item in repository_bridges
    }
    assert {
        "ToolPermissionTerminalizationProgress",
        "cancel_run",
        "classify_success_commit_block",
        "complete_run",
        "fail_run",
        "finalize_multi_agent_parent_run_if_ready",
        "list_multi_agent_parent_runs_requiring_finalization",
        "list_multi_agent_terminal_children_requiring_reconciliation",
        "list_runs_requiring_tool_permission_terminalization",
        "mark_run_enqueue_failed",
        "progress_run_tool_permission_terminalization",
        "reconcile_multi_agent_child_run_terminal_state",
        "request_admin_run_cancel",
        "request_run_cancel",
        "stage_stale_run_reconciliation",
    }.isdisjoint(
        {
            symbol
            for item in repository_bridges
            for symbol in item["symbols"]
        }
    )


def test_run_lifecycle_boundary_document_freezes_explicit_composition() -> None:
    source = (
        REPO_ROOT / "docs/architecture/run-lifecycle-boundary.md"
    ).read_text(encoding="utf-8")

    for required in (
        "RunLifecycleService",
        "StreamingEventLedgerWriter",
        "AuditLedgerWriter",
        "TerminalIntentRecorder",
        "SandboxRuntimeClient",
        "sse_terminal_publication_intents",
        "A route, transport helper, or admission helper MUST NOT import",
        "`ContextVar`, thread-local, request-local, or connection attributes",
    ):
        assert required in source


def test_undeclared_runs_lifecycle_bridge_remains_forbidden(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    source_path = repo / "app/repositories.py"
    current = source_path.read_text(encoding="utf-8")
    _write(
        repo,
        "app/runs/infrastructure/lifecycle.py",
        "async def complete_run(*args, **kwargs):\n    return True\n",
    )
    _write(
        repo,
        "app/repositories.py",
        "import app.runs.infrastructure.lifecycle as run_lifecycle_persistence\n"
        + current
        + "\ncomplete_run = run_lifecycle_persistence.complete_run\n",
    )
    head = _commit(repo, "attempt undeclared Runs lifecycle bridge")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "cross_domain_internal_import"
        and item.path == "app/repositories.py"
    )
    assert finding.exemptible is False


def test_repository_authorization_error_bridge_authority_is_exact() -> None:
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.platform.postgres.errors",
    )

    assert bridge == {
        "source_path": "app/repositories.py",
        "target_module": "app.platform.postgres.errors",
        "module_alias": "postgres_errors",
        "symbols": ["RepositoryAuthorizationError"],
        "owner": "platform-architecture",
        "reason": (
            "The frozen global repository may preserve its existing authorization "
            "error type only as an exact identity alias while the shared PostgreSQL "
            "error contract moves to the platform adapter."
        ),
        "removal_condition": (
            "After repository consumers import the platform PostgreSQL error contract, "
            "inventory supported external imports and remove this bridge in an "
            "authority-only change before deleting the repositories alias."
        ),
    }


def test_skills_persistence_bridge_authority_is_exact() -> None:
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.skills.infrastructure.postgres",
    )

    assert bridge == {
        "source_path": "app/repositories.py",
        "target_module": "app.skills.infrastructure.postgres",
        "module_alias": "skill_persistence",
        "symbols": [
            "canonical_builtin_tool_identities",
            "get_skill_version",
            "run_skill_snapshot_source_json",
            "validate_replay_skill_manifests",
        ],
        "owner": "skills",
        "reason": (
            "The frozen global repository may expose these existing Skill version, "
            "replay-validation, and run-snapshot persistence symbols only as exact "
            "identity aliases while their PostgreSQL implementation moves to the "
            "Skills adapter."
        ),
        "removal_condition": (
            "After the Skill persistence move, migrate supported internal callers to "
            "the Skills API, inventory external imports, and remove this bridge in an "
            "authority-only change before deleting the repositories aliases."
        ),
    }


@pytest.mark.parametrize(
    "target_module",
    [
        "app.platform",
        "app.platform_evil.postgres.limits",
        "app.platform.postgres.error",
        "app.platform.postgres.private",
    ],
)
def test_authority_rejects_nonexact_platform_migration_targets(
    tmp_path: Path,
    target_module: str,
) -> None:
    policy = _fixture_policy()
    bridge = next(
        entry
        for entry in policy["migration_bridges"]
        if entry["source_path"] == "app/persistence_limits.py"
    )
    bridge["target_module"] = target_module
    policy["migration_bridges"] = sorted(
        policy["migration_bridges"],
        key=lambda entry: (entry["source_path"], entry["target_module"]),
    )
    repo, authority = _create_repo(tmp_path, policy_text=json.dumps(policy))

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, authority, authority, authority)

    assert caught.value.code == "invalid_policy"


@pytest.mark.parametrize("symbol", ["not-a-python-name", "class", "_"])
def test_authority_rejects_non_python_migration_symbol(
    tmp_path: Path,
    symbol: str,
) -> None:
    policy = _fixture_policy()
    bridge = next(
        entry
        for entry in policy["migration_bridges"]
        if entry["source_path"] == "app/persistence_limits.py"
    )
    bridge["symbols"] = sorted([*bridge["symbols"], symbol])
    repo, authority = _create_repo(tmp_path, policy_text=json.dumps(policy))

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, authority, authority, authority)

    assert caught.value.code in {"invalid_policy", "invalid_policy_schema"}


def test_authority_rejects_reused_bridge_alias_within_one_source(
    tmp_path: Path,
) -> None:
    policy = _fixture_policy()
    bridges = [
        entry
        for entry in policy["migration_bridges"]
        if entry["source_path"] == "app/repositories.py"
    ]
    assert {bridge["target_module"] for bridge in bridges} == {
        "app.agent_apps.infrastructure.postgres",
        "app.context.infrastructure.postgres",
        "app.context.infrastructure.snapshot_postgres",
        "app.context.infrastructure.sources_postgres",
        "app.conversations.infrastructure.postgres",
        "app.platform.postgres.errors",
        "app.runs.infrastructure.postgres",
        "app.skills.infrastructure.postgres",
    }
    bridges[1]["module_alias"] = bridges[0]["module_alias"]
    repo, authority = _create_repo(tmp_path, policy_text=json.dumps(policy))

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, authority, authority, authority)

    assert caught.value.code == "invalid_policy"


def test_authority_rejects_reused_bridge_symbol_within_one_source(
    tmp_path: Path,
) -> None:
    policy = _fixture_policy()
    bridges = [
        entry
        for entry in policy["migration_bridges"]
        if entry["source_path"] == "app/repositories.py"
    ]
    assert {bridge["target_module"] for bridge in bridges} == {
        "app.agent_apps.infrastructure.postgres",
        "app.context.infrastructure.postgres",
        "app.context.infrastructure.snapshot_postgres",
        "app.context.infrastructure.sources_postgres",
        "app.conversations.infrastructure.postgres",
        "app.platform.postgres.errors",
        "app.runs.infrastructure.postgres",
        "app.skills.infrastructure.postgres",
    }
    duplicate_symbol = bridges[0]["symbols"][0]
    bridges[1]["symbols"] = sorted([*bridges[1]["symbols"], duplicate_symbol])
    repo, authority = _create_repo(tmp_path, policy_text=json.dumps(policy))

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, authority, authority, authority)

    assert caught.value.code == "invalid_policy"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("wrong_alias", "migration_bridge_import_contract"),
        ("missing_symbol", "migration_bridge_symbol_contract"),
        ("rebound_symbol", "migration_bridge_symbol_contract"),
        ("missing_target", "migration_bridge_target_contract"),
        ("missing_target_symbol", "migration_bridge_target_contract"),
        ("new_logic", "migration_bridge_source_logic"),
    ],
)
def test_context_memory_migration_bridge_fails_closed_on_contract_drift(
    governance_repo: tuple[Path, str], mutation: str, expected: str
) -> None:
    repo, authority = governance_repo
    _activate_context_memory_bridge(repo)
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.context.infrastructure.postgres",
    )
    source_path = repo / bridge["source_path"]
    source = source_path.read_text(encoding="utf-8")
    first_symbol = bridge["symbols"][0]
    target_path = repo / "app/context/infrastructure/postgres.py"
    if mutation == "wrong_alias":
        source = source.replace(
            f" as {bridge['module_alias']}",
            " as unauthorized_alias",
            1,
        )
    elif mutation == "missing_symbol":
        source = source.replace(
            f"{first_symbol} = {bridge['module_alias']}.{first_symbol}\n",
            "",
        )
    elif mutation == "rebound_symbol":
        source = source.replace(
            f"{first_symbol} = {bridge['module_alias']}.{first_symbol}",
            f"{first_symbol} = object()",
        )
    elif mutation == "missing_target":
        target_path.unlink()
    elif mutation == "missing_target_symbol":
        target_source = target_path.read_text(encoding="utf-8")
        target_path.write_text(
            target_source.replace(
                _fixture_bridge_definitions([first_symbol]),
                "",
                1,
            ),
            encoding="utf-8",
        )
    elif mutation == "new_logic":
        source += "\ndef newly_owned_logic():\n    return True\n"
    _write(repo, bridge["source_path"], source)
    head = _commit(repo, f"reject Context Memory bridge {mutation}")

    evaluation = _evaluate(repo, authority, authority, head)
    assert evaluation.status != "pass"

    expected_path = (
        "app/context/infrastructure/postgres.py"
        if mutation in {"missing_target", "missing_target_symbol"}
        else bridge["source_path"]
    )
    finding = next(
        item
        for item in evaluation.findings
        if item.code == expected and item.path == expected_path
    )
    assert finding.exemptible is False


@pytest.mark.parametrize(
    "source_path",
    ["app/agent_conversation_repository.py", "app/repositories.py"],
)
@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("wrong_alias", "migration_bridge_import_contract"),
        ("missing_symbol", "migration_bridge_symbol_contract"),
        ("rebound_symbol", "migration_bridge_symbol_contract"),
        ("missing_target", "migration_bridge_target_contract"),
        ("missing_target_symbol", "migration_bridge_target_contract"),
        ("new_logic", "migration_bridge_source_logic"),
    ],
)
def test_conversation_migration_bridges_fail_closed_on_contract_drift(
    governance_repo: tuple[Path, str],
    source_path: str,
    mutation: str,
    expected: str,
) -> None:
    repo, authority = governance_repo
    _activate_conversation_bridges(repo)
    bridge = _migration_bridge(
        source_path=source_path,
        target_module="app.conversations.infrastructure.postgres",
    )
    source = (repo / source_path).read_text(encoding="utf-8")
    first_symbol = bridge["symbols"][0]
    if mutation == "wrong_alias":
        source = source.replace(
            f" as {bridge['module_alias']}",
            " as unauthorized_alias",
            1,
        )
    elif mutation == "missing_symbol":
        source = source.replace(
            f"{first_symbol} = {bridge['module_alias']}.{first_symbol}\n",
            "",
        )
    elif mutation == "rebound_symbol":
        source = source.replace(
            f"{first_symbol} = {bridge['module_alias']}.{first_symbol}",
            f"{first_symbol} = object()",
        )
    elif mutation == "missing_target":
        (repo / "app/conversations/infrastructure/postgres.py").unlink()
    elif mutation == "missing_target_symbol":
        target_path = repo / "app/conversations/infrastructure/postgres.py"
        target_source = target_path.read_text(encoding="utf-8")
        target_path.write_text(
            target_source.replace(
                _fixture_async_definitions([first_symbol]),
                "",
                1,
            ),
            encoding="utf-8",
        )
    elif mutation == "new_logic":
        source += "\ndef newly_owned_logic():\n    return True\n"
    _write(repo, source_path, source)
    head = _commit(repo, f"reject Conversation bridge {mutation} at {source_path}")

    evaluation = _evaluate(repo, authority, authority, head)

    expected_path = (
        "app/conversations/infrastructure/postgres.py"
        if mutation in {"missing_target", "missing_target_symbol"}
        else source_path
    )
    finding = next(
        item
        for item in evaluation.findings
        if item.code == expected
        and item.path == expected_path
    )
    assert finding.exemptible is False


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("wrong_alias", "migration_bridge_import_contract"),
        ("missing_symbol", "migration_bridge_symbol_contract"),
        ("rebound_symbol", "migration_bridge_symbol_contract"),
        ("missing_target", "migration_bridge_target_contract"),
        ("missing_target_symbol", "migration_bridge_target_contract"),
        ("new_logic", "migration_bridge_source_logic"),
    ],
)
def test_runs_migration_bridge_fails_closed_on_contract_drift(
    governance_repo: tuple[Path, str], mutation: str, expected: str
) -> None:
    repo, authority = governance_repo
    _activate_runs_bridge(repo)
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.runs.infrastructure.postgres",
    )
    source_path = repo / bridge["source_path"]
    source = source_path.read_text(encoding="utf-8")
    first_symbol = bridge["symbols"][0]
    if mutation == "wrong_alias":
        source = source.replace(
            f" as {bridge['module_alias']}",
            " as unauthorized_alias",
            1,
        )
    elif mutation == "missing_symbol":
        source = source.replace(
            f"{first_symbol} = {bridge['module_alias']}.{first_symbol}\n",
            "",
        )
    elif mutation == "rebound_symbol":
        source = source.replace(
            f"{first_symbol} = {bridge['module_alias']}.{first_symbol}",
            f"{first_symbol} = object()",
        )
    elif mutation == "missing_target":
        (repo / "app/runs/infrastructure/postgres.py").unlink()
    elif mutation == "missing_target_symbol":
        target_path = repo / "app/runs/infrastructure/postgres.py"
        target_source = target_path.read_text(encoding="utf-8")
        target_path.write_text(
            target_source.replace(
                _fixture_async_definitions([first_symbol]),
                "",
                1,
            ),
            encoding="utf-8",
        )
    elif mutation == "new_logic":
        source += "\ndef newly_owned_logic():\n    return True\n"
    _write(repo, bridge["source_path"], source)
    head = _commit(repo, f"reject Runs bridge {mutation}")

    evaluation = _evaluate(repo, authority, authority, head)
    assert evaluation.status != "pass"

    expected_path = (
        "app/runs/infrastructure/postgres.py"
        if mutation in {"missing_target", "missing_target_symbol"}
        else bridge["source_path"]
    )
    finding = next(
        item
        for item in evaluation.findings
        if item.code == expected and item.path == expected_path
    )
    assert finding.exemptible is False


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("wrong_alias", "migration_bridge_import_contract"),
        ("missing_symbol", "migration_bridge_symbol_contract"),
        ("rebound_symbol", "migration_bridge_symbol_contract"),
        ("missing_target", "migration_bridge_target_contract"),
        ("new_logic", "migration_bridge_source_logic"),
    ],
)
def test_legacy_migration_bridge_fails_closed_on_contract_drift(
    governance_repo: tuple[Path, str], mutation: str, expected: str
) -> None:
    repo, authority = governance_repo
    _activate_agent_profile_bridge(
        repo,
        source_suffix="\ndef newly_owned_logic():\n    return True\n"
        if mutation == "new_logic"
        else "",
    )
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.agent_apps.infrastructure.postgres",
    )
    source_path = repo / bridge["source_path"]
    source = source_path.read_text(encoding="utf-8")
    first_symbol = bridge["symbols"][0]
    if mutation == "wrong_alias":
        source = source.replace(
            f" as {bridge['module_alias']}",
            " as unauthorized_alias",
            1,
        )
    elif mutation == "missing_symbol":
        source = source.replace(
            f"{first_symbol} = {bridge['module_alias']}.{first_symbol}\n",
            "",
        )
    elif mutation == "rebound_symbol":
        source = source.replace(
            f"{first_symbol} = {bridge['module_alias']}.{first_symbol}",
            f"{first_symbol} = object()",
        )
    elif mutation == "missing_target":
        (repo / "app/agent_apps/infrastructure/postgres.py").unlink()
    _write(repo, bridge["source_path"], source)
    head = _commit(repo, f"reject migration bridge {mutation}")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(item for item in evaluation.findings if item.code == expected)
    assert finding.exemptible is False


def test_undeclared_root_to_domain_internal_import_remains_forbidden(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    current = (repo / "app/repositories.py").read_text(encoding="utf-8")
    _write(
        repo,
        "app/repositories.py",
        "import app.agent_apps.infrastructure.private as hidden_profile_persistence\n"
        + current,
    )
    head = _commit(repo, "attempt undeclared persistence bridge")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "cross_domain_internal_import" in _codes(evaluation)


def test_inactive_bridge_source_cannot_be_deleted_before_authority_retirement(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    (repo / "app/repositories.py").unlink()
    head = _commit(repo, "attempt premature inactive bridge deletion")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "migration_bridge_import_contract"
    )
    assert finding.exemptible is False


def test_dynamic_import_cannot_activate_a_migration_bridge(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.agent_apps.infrastructure.postgres",
    )
    current = (repo / bridge["source_path"]).read_text(encoding="utf-8")
    _write(
        repo,
        bridge["source_path"],
        f"{bridge['module_alias']} = __import__({bridge['target_module']!r})\n" + current,
    )
    head = _commit(repo, "attempt dynamic migration bridge")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "migration_bridge_import_contract" in _codes(evaluation)


def test_obfuscated_dynamic_import_cannot_replace_declared_definitions(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.agent_apps.infrastructure.postgres",
    )
    _write(
        repo,
        bridge["source_path"],
        "import importlib\n"
        f"{bridge['module_alias']} = importlib.import_module(\n"
        "    'app.agent_apps.infrastructure.' + 'postgres'\n"
        ")\n"
        "DEFAULT_RUN_EXECUTOR_TYPES = {\"claude-agent-worker\"}\n"
        + "\n".join(
            f"{name} = {bridge['module_alias']}.{name}"
            for name in bridge["symbols"]
        )
        + "\n",
    )
    head = _commit(repo, "attempt obfuscated dynamic migration bridge")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "migration_bridge_import_contract" in _codes(evaluation)


def test_obfuscated_dynamic_import_with_undeclared_alias_is_rejected(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    current = (repo / "app/repositories.py").read_text(encoding="utf-8")
    _write(
        repo,
        "app/repositories.py",
        "import importlib\n"
        "unauthorized_alias = importlib.import_module(\n"
        "    'app.agent_apps.infrastructure.' + 'postgres'\n"
        ")\n"
        + current,
    )
    head = _commit(repo, "attempt obfuscated undeclared bridge alias")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "migration_bridge_import_contract" in _codes(evaluation)


@pytest.mark.parametrize(
    "loader_binding",
    [
        "import importlib\n_loader = importlib.import_module\n",
        "from importlib import import_module as _loader\n",
    ],
)
def test_aliased_dynamic_import_cannot_bypass_a_migration_bridge(
    governance_repo: tuple[Path, str],
    loader_binding: str,
) -> None:
    repo, authority = governance_repo
    current = (repo / "app/repositories.py").read_text(encoding="utf-8")
    _write(
        repo,
        "app/repositories.py",
        loader_binding
        + "_private = _loader('app.' + 'agent_apps.infrastructure.' + 'postgres')\n"
        "unauthorized_alias = _private\n"
        + current,
    )
    head = _commit(repo, "attempt aliased dynamic migration bridge")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(
        item
        for item in evaluation.findings
        if item.code == "migration_bridge_import_contract"
        and item.path == "app/repositories.py"
    )
    assert finding.exemptible is False
    assert {"import_module", "importlib"} <= set(
        finding.details["dynamic_import_capabilities"]
    )


def test_bridge_activation_must_strictly_shrink_the_legacy_source(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_agent_profile_bridge(repo)
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.agent_apps.infrastructure.postgres",
    )
    source_path = repo / bridge["source_path"]
    source = source_path.read_text(encoding="utf-8")
    base_lines = len(
        _git(repo, "show", f"{authority}:{bridge['source_path']}").splitlines()
    )
    current_lines = len(source.splitlines())
    source_path.write_text(
        source + "\n".join("# padding" for _ in range(base_lines - current_lines + 1)) + "\n",
        encoding="utf-8",
    )
    head = _commit(repo, "attempt non-shrinking bridge activation")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "migration_bridge_source_growth" in _codes(evaluation)


def test_active_bridge_rechecks_target_even_when_only_target_changes(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _activate_agent_profile_bridge(repo)
    active_base = _commit(repo, "activate bridge")
    bridge = _migration_bridge(
        source_path="app/repositories.py",
        target_module="app.agent_apps.infrastructure.postgres",
    )
    target_path = repo / "app/agent_apps/infrastructure/postgres.py"
    target = target_path.read_text(encoding="utf-8")
    target_path.write_text(
        target.replace(f"async def {bridge['symbols'][0]}():", "async def removed_symbol():", 1),
        encoding="utf-8",
    )
    head = _commit(repo, "break active bridge target")

    evaluation = _evaluate(repo, authority, active_base, head)

    assert "migration_bridge_target_contract" in _codes(evaluation)


@pytest.mark.parametrize(
    "addition",
    [
        "\ndef newly_owned_logic():\n    return True\n",
        "\nimport app.agent_apps.infrastructure.postgres as alternate_owner\n",
        "\nfrom app.agent_apps.infrastructure.postgres import record_agent_profile_draft\n",
        "\nif True:\n    BRIDGE_SIDE_EFFECT = True\n",
    ],
)
def test_active_bridge_rejects_later_source_logic_and_extra_imports(
    governance_repo: tuple[Path, str], addition: str
) -> None:
    repo, authority = governance_repo
    _activate_agent_profile_bridge(repo)
    active_base = _commit(repo, "activate bridge")
    source_path = repo / "app/repositories.py"
    source_path.write_text(
        source_path.read_text(encoding="utf-8") + addition,
        encoding="utf-8",
    )
    head = _commit(repo, "attempt post-activation bridge growth")

    evaluation = _evaluate(repo, authority, active_base, head)

    assert "migration_bridge_source_logic" in _codes(evaluation)
    assert "migration_bridge_source_growth" in _codes(evaluation)


@pytest.mark.parametrize(
    "source",
    [
        "from app.agent_apps.infrastructure.postgres import record_agent_profile_draft\n",
        "if True:\n    import app.agent_apps.infrastructure.postgres as agent_profile_persistence\n",
    ],
)
def test_noncanonical_import_forms_do_not_bypass_cross_domain_enforcement(
    governance_repo: tuple[Path, str], source: str
) -> None:
    repo, authority = governance_repo
    current = (repo / "app/repositories.py").read_text(encoding="utf-8")
    _write(repo, "app/repositories.py", source + current)
    head = _commit(repo, "attempt noncanonical bridge import")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "cross_domain_internal_import" in _codes(evaluation)


def test_candidate_cannot_self_authorize_an_undeclared_bridge(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    relaxed = _fixture_policy()
    relaxed_bridge = next(
        bridge
        for bridge in relaxed["migration_bridges"]
        if bridge["source_path"] == "app/repositories.py"
        and bridge["target_module"] == "app.agent_apps.infrastructure.postgres"
    )
    relaxed_bridge["target_module"] = "app.agent_apps.infrastructure.private"
    _write(repo, "architecture-policy.json", json.dumps(relaxed, indent=2))
    current = (repo / "app/repositories.py").read_text(encoding="utf-8")
    _write(
        repo,
        "app/repositories.py",
        "import app.agent_apps.infrastructure.private as agent_profile_persistence\n"
        + current,
    )
    head = _commit(repo, "attempt candidate bridge self authorization")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "cross_domain_internal_import" in _codes(evaluation)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda bridge: bridge.update(target_module="app.agent_apps.infrastructure_evil.postgres"),
        lambda bridge: bridge.update(symbols=list(reversed(bridge["symbols"]))),
        lambda bridge: bridge.update(module_alias="DynamicAlias"),
    ],
)
def test_authority_rejects_broad_or_unsorted_migration_bridge_contracts(
    tmp_path: Path, mutate: Any
) -> None:
    policy = _fixture_policy()
    bridge = next(
        entry
        for entry in policy["migration_bridges"]
        if entry["source_path"] == "app/repositories.py"
        and entry["target_module"] == "app.agent_apps.infrastructure.postgres"
    )
    mutate(bridge)
    repo, authority = _create_repo(tmp_path, policy_text=json.dumps(policy))

    with pytest.raises(architecture_governance.ArchitectureError) as caught:
        _evaluate(repo, authority, authority, authority)

    assert caught.value.code == "invalid_policy"


def test_unapproved_root_and_generic_modules_are_rejected(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/new_bucket.py", "VALUE = True\n")
    _write(repo, "app/runs/helpers.py", "VALUE = True\n")
    head = _commit(repo, "bad module placement")

    evaluation = _evaluate(repo, authority, authority, head)

    assert {"generic_module_name", "unapproved_app_root_module"} <= _codes(evaluation)


def test_frozen_hot_file_growth_is_a_candidate_bound_finding(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/worker.py", "BASELINE = True\nNEW_RESPONSIBILITY = True\n")
    head = _commit(repo, "grow frozen worker")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(item for item in evaluation.findings if item.code == "frozen_hot_file_growth")
    assert finding.exemptible is True
    assert finding.details["base_lines"] == 1
    assert finding.details["head_lines"] == 2


@pytest.mark.parametrize(
    ("addition", "expected"),
    [
        ("\nSQL = \"SELECT * FROM files\"\n", "facade_sql"),
        ("\nif True:\n    FLAG = True\n", "facade_control_flow"),
        ("\ndef dispatch():\n    return queue.publish()\n", "facade_runtime_logic"),
        ("\nfrom app.runtime.sandbox import provider\n", "facade_import_forbidden"),
    ],
)
def test_logic_free_facade_rejects_sql_control_flow_provider_and_queue_logic(
    governance_repo: tuple[Path, str], addition: str, expected: str
) -> None:
    repo, authority = governance_repo
    current = (repo / "app" / "artifact_lifecycle_repository.py").read_text(encoding="utf-8")
    _write(repo, "app/artifact_lifecycle_repository.py", current + addition)
    head = _commit(repo, expected)

    evaluation = _evaluate(repo, authority, authority, head)

    assert expected in _codes(evaluation)


def test_logic_free_facade_requires_static_all_and_docstring_only_expressions(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/artifact_lifecycle_repository.py",
        "from app.persistence.object_deletions import claim_object_deletions\n"
        "exported_names = [\"claim_object_deletions\"]\n"
        "__all__ = [name for name in exported_names]\n",
    )
    head = _commit(repo, "dynamic facade exports")

    evaluation = _evaluate(repo, authority, authority, head)

    assert {"facade_export_contract", "facade_local_state"} <= _codes(evaluation)


def test_non_all_assignment_cannot_satisfy_facade_export_contract(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/artifact_lifecycle_repository.py",
        "from app.persistence.object_deletions import claim_object_deletions\n"
        "exported_names = [\"claim_object_deletions\"]\n",
    )
    head = _commit(repo, "omit facade dunder all")

    evaluation = _evaluate(repo, authority, authority, head)

    assert {"facade_export_contract", "facade_local_state"} <= _codes(evaluation)


def test_logic_free_facade_rejects_nonexistent_export_and_prefix_collision(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/artifact_lifecycle_repository.py",
        "from app.persistence_evil.object_deletions import claim_object_deletions\n"
        "__all__ = [\"nonexistent\"]\n",
    )
    head = _commit(repo, "invalid facade export")

    evaluation = _evaluate(repo, authority, authority, head)

    assert {"facade_export_contract", "facade_import_forbidden"} <= _codes(evaluation)


def test_logic_free_facade_rejects_wildcard_import_and_export(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/artifact_lifecycle_repository.py",
        "from app.persistence import *\n__all__ = [\"*\"]\n",
    )
    head = _commit(repo, "wildcard facade")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "facade_wildcard_import" in _codes(evaluation)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter\n"
            "def _default_adapters():\n"
            "    return {\"claude-agent-worker\": ClaudeAgentWorkerAdapter(), \"claude-agent-worker\": ClaudeAgentWorkerAdapter()}\n",
            "registry_duplicate_key",
        ),
        (
            "from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter\n"
            "def _default_adapters():\n"
            "    return {\"claude-agent-worker\": ClaudeAgentWorkerAdapter(), \"unknown-worker\": ClaudeAgentWorkerAdapter()}\n",
            "registry_unknown_key",
        ),
        (
            "class FakeAdapter: pass\n"
            "def _default_adapters():\n"
            "    return {\"claude-agent-worker\": FakeAdapter()}\n",
            "registry_test_double",
        ),
        (
            "def _default_adapters(module_name):\n"
            "    adapter = getattr(__import__(module_name), \"Adapter\")\n"
            "    return {\"claude-agent-worker\": ClaudeAgentWorkerAdapter()}\n",
            "registry_dynamic_selector",
        ),
    ],
)
def test_production_registry_is_closed_and_static(
    governance_repo: tuple[Path, str], source: str, expected: str
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/executors/registry.py", source)
    head = _commit(repo, expected)

    evaluation = _evaluate(repo, authority, authority, head)

    assert expected in _codes(evaluation)


def test_registry_ignores_unrelated_metadata_dict_but_checks_factory_values(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/executors/registry.py",
        "METADATA = {\"description\": \"production\"}\n"
        "def _default_adapters():\n"
        "    return {\"claude-agent-worker\": \"fake\"}\n",
    )
    head = _commit(repo, "registry value test double")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "registry_test_double" in _codes(evaluation)
    assert "registry_unknown_key" not in _codes(evaluation)


def test_registry_rejects_qualified_or_local_constructor_spoof(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/executors/registry.py",
        "class ClaudeAgentWorkerAdapter: pass\n"
        "def _default_adapters():\n"
        "    return {\"claude-agent-worker\": ClaudeAgentWorkerAdapter()}\n",
    )
    head = _commit(repo, "spoof adapter constructor")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "registry_adapter_mismatch" in _codes(evaluation)


def test_registry_rejects_import_then_local_constructor_rebinding(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/executors/registry.py",
        "from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter\n"
        "class ClaudeAgentWorkerAdapter: pass\n"
        "def _default_adapters():\n"
        "    return {\"claude-agent-worker\": ClaudeAgentWorkerAdapter()}\n",
    )
    head = _commit(repo, "rebind imported adapter")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "registry_adapter_mismatch" in _codes(evaluation)


def test_registry_rejects_control_flow_or_factory_local_constructor_rebinding(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/executors/registry.py",
        "from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter\n"
        "if True:\n"
        "    ClaudeAgentWorkerAdapter = LocalAdapter\n"
        "def _default_adapters():\n"
        "    return {\"claude-agent-worker\": ClaudeAgentWorkerAdapter()}\n",
    )
    control_flow_head = _commit(repo, "control flow rebinds adapter")

    control_flow = _evaluate(repo, authority, authority, control_flow_head)

    assert "registry_adapter_mismatch" in _codes(control_flow)

    _write(
        repo,
        "app/executors/registry.py",
        "from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter\n"
        "def _default_adapters():\n"
        "    ClaudeAgentWorkerAdapter = LocalAdapter\n"
        "    return {\"claude-agent-worker\": ClaudeAgentWorkerAdapter()}\n",
    )
    factory_head = _commit(repo, "factory rebinds adapter")

    factory = _evaluate(repo, authority, control_flow_head, factory_head)

    assert "registry_factory_contract" in _codes(factory)


def test_registry_rejects_arbitrary_os_command_selector(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/executors/registry.py",
        "def _default_adapters(command):\n"
        "    return {\"claude-agent-worker\": os.system(command)}\n",
    )
    head = _commit(repo, "arbitrary registry command")

    evaluation = _evaluate(repo, authority, authority, head)

    assert "registry_dynamic_selector" in _codes(evaluation)


def test_registry_selector_owner_must_match_declared_keys(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/repositories.py", "DEFAULT_RUN_EXECUTOR_TYPES = {\"fake\"}\n")
    head = _commit(repo, "selector drift")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(item for item in evaluation.findings if item.code == "registry_selector_mismatch")
    assert finding.exemptible is False


def test_governed_symbol_cannot_gain_a_second_owner(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/runs/domain/targets.py", "OUTBOX_TARGET_FILE = \"file\"\n")
    head = _commit(repo, "duplicate governed symbol")

    evaluation = _evaluate(repo, authority, authority, head)

    finding = next(item for item in evaluation.findings if item.code == "governed_symbol_owner")
    assert finding.exemptible is False


def test_governed_symbol_owner_deletion_is_rejected(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _git(repo, "rm", "app/persistence/object_deletions.py")
    head = _commit(repo, "delete governed owner")

    evaluation = _evaluate(repo, authority, authority, head)

    assert [item.code for item in evaluation.findings].count("governed_symbol_missing") == 2


def test_tuple_and_dynamic_governed_symbol_definitions_are_detected(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/runs/domain/targets.py",
        "OUTBOX_TARGET_FILE, other = (\"file\", 1)\n"
        "globals()[\"OUTBOX_TARGET_ARTIFACT\"] = \"artifact\"\n",
    )
    head = _commit(repo, "dynamic governed symbols")

    evaluation = _evaluate(repo, authority, authority, head)

    assert [item.code for item in evaluation.findings].count("governed_symbol_owner") == 2


def _exception_scope(repo: Path, base: str, head: str) -> str:
    reader = architecture_governance._GitObjects(
        repo,
        architecture_governance._CommandRunner(),
    )
    return reader.exception_scope_sha256(base, head, ".architecture-governance-exception.json")


def _exception_payload(
    authority: str,
    base: str,
    scope_sha256: str,
    *,
    code: str = "frozen_hot_file_growth",
    path: str = "app/worker.py",
    expires_on: str = "2026-08-20",
) -> dict[str, Any]:
    return {
        "schema_version": "ai-platform.architecture-governance-exception.v1",
        "candidate": {
            "authority_ref": authority,
            "base_ref": base,
            "head_scope_sha256": scope_sha256,
        },
        "expires_on": expires_on,
        "owner": "execution",
        "reason": "A bounded security fix must land before the extraction slice.",
        "removal_condition": "Remove after the execution owner extraction merges.",
        "paths": [path],
        "violations": [{"code": code, "path": path}],
    }


def test_exact_exception_exempts_only_the_bound_hot_file_growth(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/worker.py", "BASELINE = True\nSECURITY_FIX = True\n")
    scope_head = _commit(repo, "exception scope")
    payload = _exception_payload(authority, authority, _exception_scope(repo, authority, scope_head))
    _write(repo, ".architecture-governance-exception.json", json.dumps(payload, indent=2))
    head = _commit(repo, "bind architecture exception")

    evaluation = _evaluate(repo, authority, authority, head)

    assert evaluation.status == "pass"
    assert [item.code for item in evaluation.exempted_findings] == ["frozen_hot_file_growth"]
    assert evaluation.exception["status"] == "applied"


def test_inherited_exception_is_inactive_and_cannot_exempt_new_findings(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/worker.py", "BASELINE = True\nSECURITY_FIX = True\n")
    scope_head = _commit(repo, "exception scope")
    payload = _exception_payload(authority, authority, _exception_scope(repo, authority, scope_head))
    _write(repo, ".architecture-governance-exception.json", json.dumps(payload, indent=2))
    exception_base = _commit(repo, "bind architecture exception")
    assert _evaluate(repo, authority, authority, exception_base).status == "pass"

    _write(repo, "app/worker.py", "BASELINE = True\nSECURITY_FIX = True\nMORE_GROWTH = True\n")
    head = _commit(repo, "grow inherited hot file again")

    evaluation = _evaluate(repo, exception_base, exception_base, head)

    assert evaluation.status == "violation"
    assert [item.code for item in evaluation.findings] == ["frozen_hot_file_growth"]
    assert evaluation.exempted_findings == ()
    assert evaluation.exception["status"] == "inherited_inactive"


def test_exception_rejects_ambiguous_same_code_and_path_findings(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    _write(
        repo,
        "app/artifact_lifecycle_repository.py",
        "from app.runtime import first\nfrom app.routes import second\n__all__ = []\n",
    )
    scope_head = _commit(repo, "ambiguous facade findings")
    payload = _exception_payload(
        authority,
        authority,
        _exception_scope(repo, authority, scope_head),
        code="facade_import_forbidden",
        path="app/artifact_lifecycle_repository.py",
    )
    _write(repo, ".architecture-governance-exception.json", json.dumps(payload))
    head = _commit(repo, "ambiguous exception")

    with pytest.raises(architecture_governance.ArchitectureError, match="exactly one finding") as caught:
        _evaluate(repo, authority, authority, head)

    assert caught.value.code == "invalid_exception"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.update(expires_on="2026-08-01"), "expired"),
        (
            lambda payload: payload["candidate"].update(head_scope_sha256="0" * 64),
            "exact patch",
        ),
    ],
)
def test_stale_or_wrong_scope_exception_fails_closed(
    governance_repo: tuple[Path, str], mutate: Any, message: str
) -> None:
    repo, authority = governance_repo
    _write(repo, "app/worker.py", "BASELINE = True\nSECURITY_FIX = True\n")
    scope_head = _commit(repo, "exception scope")
    payload = _exception_payload(authority, authority, _exception_scope(repo, authority, scope_head))
    mutate(payload)
    _write(repo, ".architecture-governance-exception.json", json.dumps(payload))
    head = _commit(repo, "invalid exception")

    with pytest.raises(architecture_governance.ArchitectureError, match=message) as caught:
        _evaluate(repo, authority, authority, head)

    assert caught.value.code == "invalid_exception"


def test_exception_cannot_waive_non_exemptible_authority_boundary(
    governance_repo: tuple[Path, str],
) -> None:
    repo, authority = governance_repo
    path = "app/skills/application/publish.py"
    _write(repo, path, "from app.runs.domain import attempt\n")
    scope_head = _commit(repo, "non-exemptible scope")
    payload = _exception_payload(
        authority,
        authority,
        _exception_scope(repo, authority, scope_head),
        code="cross_domain_internal_import",
        path=path,
    )
    _write(repo, ".architecture-governance-exception.json", json.dumps(payload))
    head = _commit(repo, "try to waive authority boundary")

    with pytest.raises(architecture_governance.ArchitectureError, match="non-exemptible") as caught:
        _evaluate(repo, authority, authority, head)

    assert caught.value.code == "invalid_exception"


def test_diagnostics_are_stable_and_sorted(governance_repo: tuple[Path, str]) -> None:
    repo, authority = governance_repo
    _write(repo, "app/zeta.py", "VALUE = True\n")
    _write(repo, "app/runs/helpers.py", "VALUE = True\n")
    head = _commit(repo, "stable diagnostics")

    first = _evaluate(repo, authority, authority, head)
    second = _evaluate(repo, authority, authority, head)
    first_json = json.dumps(first.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    second_json = json.dumps(second.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)

    assert first_json == second_json
    assert [(item.path, item.line, item.code) for item in first.findings] == sorted(
        (item.path, item.line, item.code) for item in first.findings
    )
    assert architecture_governance._render_text(first).startswith(
        "architecture-governance: VIOLATION\n"
    )


def test_cli_exit_codes_are_zero_two_and_three(
    governance_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, authority = governance_repo
    _write(repo, "README.md", "candidate\n")
    passing_head = _commit(repo, "passing candidate")
    monkeypatch.chdir(repo)
    evaluator_type = architecture_governance.ArchitectureEvaluator
    monkeypatch.setattr(
        architecture_governance,
        "ArchitectureEvaluator",
        lambda repo_root: evaluator_type(
            repo_root,
            tool_path=TOOL_PATH,
            today=date(2026, 8, 13),
        ),
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert architecture_governance.main(
            [
                "check",
                "--authority-ref",
                authority,
                "--base-ref",
                authority,
                "--head-ref",
                passing_head,
                "--format",
                "json",
            ]
        ) == 0
    assert json.loads(stdout.getvalue())["status"] == "pass"

    _write(repo, "app/new_root.py", "VALUE = True\n")
    violating_head = _commit(repo, "violating candidate")
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert architecture_governance.main(
            [
                "check",
                "--authority-ref",
                authority,
                "--base-ref",
                passing_head,
                "--head-ref",
                violating_head,
                "--format",
                "json",
            ]
        ) == 2
    assert json.loads(stdout.getvalue())["status"] == "violation"

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert architecture_governance.main(
            [
                "check",
                "--authority-ref",
                "main",
                "--base-ref",
                passing_head,
                "--head-ref",
                violating_head,
                "--format=json",
            ]
        ) == 3
    assert json.loads(stdout.getvalue())["error"]["code"] == "invalid_ref"
