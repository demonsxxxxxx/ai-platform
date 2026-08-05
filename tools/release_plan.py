"""Pure runtime-change planning and backend dependency-contract comparison."""

from __future__ import annotations

from dataclasses import dataclass
import re
import tomllib
from typing import Any, Iterable, Mapping, Sequence


FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
BACKEND_DEPENDENCY_PATHS = frozenset({"pyproject.toml", "uv.lock", "Dockerfile"})
FRONTEND_DEPENDENCY_PATHS = frozenset(
    {
        "frontend/web/.npmrc",
        "frontend/web/package.json",
        "frontend/web/pnpm-lock.yaml",
        "frontend/web/pnpm-workspace.yaml",
        "frontend/web/Dockerfile",
    }
)
BACKEND_SOURCE_PREFIXES = (
    "app/",
    "tools/",
    "scripts/",
    "skills/",
    "docs/release-evidence/",
)
BACKEND_SOURCE_PATHS = frozenset({"docker-entrypoint.sh"})
FRONTEND_SOURCE_PREFIX = "frontend/web/"


class ReleasePlanError(ValueError):
    """Raised when pure release-plan inputs cannot be safely classified."""


@dataclass(frozen=True)
class BackendRuntimeDependencyContract:
    """The exact backend pyproject fields used to define its runtime contract."""

    dependencies: tuple[str, ...]
    requires_python: str


@dataclass(frozen=True)
class RuntimeChangeSet:
    """Classified runtime-affecting paths between a verified live commit and target."""

    backend_dependency: tuple[str, ...]
    backend_source: tuple[str, ...]
    frontend_dependency: tuple[str, ...]
    frontend_source: tuple[str, ...]
    deployment_only: tuple[str, ...]


@dataclass(frozen=True)
class RolePlan:
    """One deterministic role action selected from a classified change set."""

    role: str
    change_kind: str
    action: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class AutoReleasePlan:
    """Compact, role-specific release plan for one current-runtime to target transition."""

    current_commit: str
    target_commit: str
    changes: RuntimeChangeSet
    roles: tuple[RolePlan, ...]
    no_runtime_change: bool


def parse_backend_runtime_dependency_contract(
    pyproject_blob: str | bytes,
) -> BackendRuntimeDependencyContract:
    """Parse the exact pyproject fields the backend runtime dependency contract requires."""
    payload = _parse_pyproject(pyproject_blob)
    project = _mapping(payload.get("project"), "project")
    requires_python = project.get("requires-python")
    if not isinstance(requires_python, str) or not requires_python.strip():
        raise ReleasePlanError("pyproject runtime contract requires a non-empty project.requires-python")
    dependencies = _string_list(project.get("dependencies"), "project.dependencies")
    _validate_optional_dependencies(project)
    tool_metadata = payload.get("tool")
    if tool_metadata is not None:
        _mapping(tool_metadata, "tool")
    return BackendRuntimeDependencyContract(
        dependencies=dependencies,
        requires_python=requires_python,
    )


def is_runtime_neutral_backend_pyproject_change(
    current_pyproject_blob: str | bytes,
    target_pyproject_blob: str | bytes,
) -> bool:
    """Return whether only test or tool metadata changed outside an equal runtime contract."""
    current_payload = _parse_pyproject(current_pyproject_blob)
    target_payload = _parse_pyproject(target_pyproject_blob)
    current_contract = parse_backend_runtime_dependency_contract(current_pyproject_blob)
    target_contract = parse_backend_runtime_dependency_contract(target_pyproject_blob)
    if current_contract != target_contract:
        return False
    return (
        _runtime_metadata_projection(current_payload) == _runtime_metadata_projection(target_payload)
        and _optional_dependencies_without_test(current_payload)
        == _optional_dependencies_without_test(target_payload)
    )


def classify_runtime_changes(
    paths: Sequence[str],
    *,
    runtime_neutral_backend_dependency_paths: Iterable[str] = (),
) -> RuntimeChangeSet:
    """Classify changed paths, allowing only verified pyproject neutrality as an exception."""
    neutral_paths = frozenset(runtime_neutral_backend_dependency_paths)
    if neutral_paths - {"pyproject.toml"}:
        raise ReleasePlanError(
            "only pyproject.toml may be neutralized from backend dependency classification"
        )
    categories: dict[str, list[str]] = {
        "backend_dependency": [],
        "backend_source": [],
        "frontend_dependency": [],
        "frontend_source": [],
        "deployment_only": [],
    }
    for path in sorted(set(paths)):
        if path in BACKEND_DEPENDENCY_PATHS and path not in neutral_paths:
            categories["backend_dependency"].append(path)
        elif path in FRONTEND_DEPENDENCY_PATHS:
            categories["frontend_dependency"].append(path)
        elif path in BACKEND_SOURCE_PATHS or path.startswith(BACKEND_SOURCE_PREFIXES):
            categories["backend_source"].append(path)
        elif path.startswith(FRONTEND_SOURCE_PREFIX):
            categories["frontend_source"].append(path)
        else:
            categories["deployment_only"].append(path)
    return RuntimeChangeSet(**{name: tuple(value) for name, value in categories.items()})


def build_auto_release_plan(
    current_commit: str,
    target_commit: str,
    changes: RuntimeChangeSet,
) -> AutoReleasePlan:
    """Plan canonical builds for dependencies and economical rebuilds for source-only changes."""
    current = _normalize_commit(current_commit)
    target = _normalize_commit(target_commit)

    def role_plan(role: str, dependency: tuple[str, ...], source: tuple[str, ...]) -> RolePlan:
        if dependency:
            return RolePlan(role, "dependency", "canonical-build", dependency)
        if source:
            action = "runtime-rebuild" if role == "backend" else "source-build"
            return RolePlan(role, "source", action, source)
        return RolePlan(role, "unchanged", "reuse" if current == target else "promote", ())

    roles = (
        role_plan("backend", changes.backend_dependency, changes.backend_source),
        role_plan("frontend", changes.frontend_dependency, changes.frontend_source),
    )
    return AutoReleasePlan(
        current_commit=current,
        target_commit=target,
        changes=changes,
        roles=roles,
        no_runtime_change=not any(
            (
                changes.backend_dependency,
                changes.backend_source,
                changes.frontend_dependency,
                changes.frontend_source,
            )
        ),
    )


def _parse_pyproject(pyproject_blob: str | bytes) -> dict[str, Any]:
    if isinstance(pyproject_blob, bytes):
        try:
            text = pyproject_blob.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReleasePlanError("pyproject runtime contract is not valid UTF-8") from exc
    elif isinstance(pyproject_blob, str):
        text = pyproject_blob
    else:
        raise ReleasePlanError("pyproject runtime contract blob is missing or unreadable")
    try:
        parsed = tomllib.loads(text)
    except (TypeError, tomllib.TOMLDecodeError) as exc:
        raise ReleasePlanError("pyproject runtime contract is malformed") from exc
    if not isinstance(parsed, dict):
        raise ReleasePlanError("pyproject runtime contract has an invalid root type")
    return parsed


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ReleasePlanError(f"pyproject runtime contract has an invalid {name} type")
    return value


def _string_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ReleasePlanError(f"pyproject runtime contract has an invalid {name} type")
    return tuple(value)


def _validate_optional_dependencies(project: Mapping[str, Any]) -> None:
    optional = project.get("optional-dependencies")
    if optional is None:
        return
    for group, dependencies in _mapping(optional, "project.optional-dependencies").items():
        if not group.strip():
            raise ReleasePlanError("pyproject runtime contract has an invalid optional dependency group")
        _string_list(dependencies, f"project.optional-dependencies.{group}")


def _runtime_metadata_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    projected = dict(payload)
    project = dict(_mapping(payload.get("project"), "project"))
    project.pop("optional-dependencies", None)
    projected["project"] = project
    projected.pop("tool", None)
    return projected


def _optional_dependencies_without_test(payload: Mapping[str, Any]) -> dict[str, Any]:
    project = _mapping(payload.get("project"), "project")
    optional = project.get("optional-dependencies")
    if optional is None:
        return {}
    validated = _mapping(optional, "project.optional-dependencies")
    return {name: value for name, value in validated.items() if name != "test"}


def _normalize_commit(value: str) -> str:
    if not isinstance(value, str):
        raise ReleasePlanError("release commit must be a full 40-character lowercase SHA")
    commit = value.strip().lower()
    if not FULL_COMMIT_RE.fullmatch(commit):
        raise ReleasePlanError("release commit must be a full 40-character lowercase SHA")
    return commit
