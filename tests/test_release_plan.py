import pytest

from tools.release_plan import (
    ReleasePlanError,
    build_auto_release_plan,
    classify_runtime_changes,
    is_runtime_neutral_backend_pyproject_change,
    parse_backend_runtime_dependency_contract,
)


def _pyproject(
    *,
    dependencies: str = '"fastapi==0.111.0"',
    requires_python: str = ">=3.11",
    test_dependencies: str = '"pytest==8.2.0"',
) -> str:
    return f'''\
[project]
name = "ai-platform"
requires-python = "{requires_python}"
dependencies = [{dependencies}]

[project.optional-dependencies]
test = [{test_dependencies}]
'''


def test_optional_test_only_pyproject_change_is_runtime_neutral_and_promotes_backend():
    current = _pyproject()
    target = _pyproject(test_dependencies='"pytest==8.2.0", "ruff==0.11.13"')

    contract = parse_backend_runtime_dependency_contract(current)
    changes = classify_runtime_changes(
        ["pyproject.toml"],
        runtime_neutral_backend_dependency_paths=("pyproject.toml",),
    )
    plan = build_auto_release_plan("1" * 40, "2" * 40, changes)

    assert contract.dependencies == ("fastapi==0.111.0",)
    assert contract.requires_python == ">=3.11"
    assert is_runtime_neutral_backend_pyproject_change(current, target) is True
    assert plan.roles[0].change_kind == "unchanged"
    assert plan.roles[0].action == "promote"


@pytest.mark.parametrize(
    "target",
    [
        _pyproject(dependencies='"fastapi==0.111.0", "redis==5.0.0"'),
        _pyproject(requires_python=">=3.12"),
    ],
)
def test_runtime_contract_change_is_not_neutral(target):
    assert is_runtime_neutral_backend_pyproject_change(_pyproject(), target) is False


@pytest.mark.parametrize(
    "blob",
    [
        None,
        "[project",
        "[project]\nrequires-python = true\ndependencies = []\n",
        "[project]\nrequires-python = \">=3.11\"\ndependencies = \"fastapi\"\n",
        "[project]\nrequires-python = \">=3.11\"\ndependencies = [\"\"]\n",
        (
            "[project]\nrequires-python = \">=3.11\"\ndependencies = []\n"
            "[project.optional-dependencies]\ntest = \"pytest\"\n"
        ),
    ],
)
def test_invalid_or_missing_pyproject_contract_fails_closed(blob):
    with pytest.raises(ReleasePlanError):
        parse_backend_runtime_dependency_contract(blob)


def test_only_pyproject_can_be_neutralized_from_dependency_classification():
    with pytest.raises(ReleasePlanError, match="only pyproject.toml"):
        classify_runtime_changes(
            ["Dockerfile"],
            runtime_neutral_backend_dependency_paths=("Dockerfile",),
        )


def test_neutral_pyproject_with_backend_source_selects_runtime_rebuild():
    changes = classify_runtime_changes(
        ["pyproject.toml", "app/main.py"],
        runtime_neutral_backend_dependency_paths=("pyproject.toml",),
    )
    plan = build_auto_release_plan("1" * 40, "2" * 40, changes)

    assert changes.backend_dependency == ()
    assert changes.backend_source == ("app/main.py",)
    assert changes.deployment_only == ("pyproject.toml",)
    assert (plan.roles[0].change_kind, plan.roles[0].action) == ("source", "runtime-rebuild")


def test_existing_backend_and_frontend_paths_keep_their_role_actions():
    changes = classify_runtime_changes(["Dockerfile", "frontend/web/src/App.tsx"])
    plan = build_auto_release_plan("1" * 40, "2" * 40, changes)

    assert (plan.roles[0].change_kind, plan.roles[0].action) == ("dependency", "canonical-build")
    assert (plan.roles[1].change_kind, plan.roles[1].action) == ("source", "source-build")
