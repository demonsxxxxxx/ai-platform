from __future__ import annotations

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


def _fixture_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


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
        policy_text if policy_text is not None else POLICY_PATH.read_text(encoding="utf-8"),
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
    _write(
        repo,
        "app/repositories.py",
        "DEFAULT_RUN_EXECUTOR_TYPES = {\"claude-agent-worker\"}\n",
    )
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
    _write(repo, f"app/runs/{boundary}.py", "VALUE = True\n")
    head = _commit(repo, f"add runs {boundary} boundary")

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
    policy["public_kernel_modules"] = ["identity"]
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
    policy["public_kernel_modules"] = ["identity"]
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
    policy["public_kernel_modules"] = ["identity"]
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
