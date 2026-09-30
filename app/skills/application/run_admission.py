"""Application orchestration for immutable Skill run admission."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.skills.domain.executable_names import is_valid_executable_skill_name


MAX_SKILL_RUN_MANIFESTS = 64


class SkillRunVersionMismatch(ValueError):
    """The materialized Skill differs from the caller's admitted version."""


@dataclass(frozen=True)
class SkillRunAdmission:
    skill_manifests: list[dict[str, Any]]
    skill_version: str
    release_decision: dict[str, Any]


class SkillCatalog(Protocol):
    async def list_public_skill_catalog(
        self,
        conn: Any,
        *,
        tenant_id: str,
        include_disabled: bool,
        rollout_key: str,
        skill_ids: list[str],
    ) -> list[dict[str, Any]]: ...


class SkillVersions(Protocol):
    async def get_effective_skill_version_for_policy(
        self, conn: Any, *, skill_id: str, version: str
    ) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class SkillRunAdmissionPorts:
    catalog: SkillCatalog
    versions: SkillVersions
    resolve_release_decision: Callable[..., Any]
    release_decision_payload: Callable[..., dict[str, Any]]
    is_user_runnable_status: Callable[[Any], bool]
    dependency_is_usable: Callable[..., bool]
    build_manifest_pins: Callable[..., list[dict[str, Any]]]
    lock_skill_version: Callable[..., str]
    attach_snapshot_governance: Callable[..., list[dict[str, Any]]]
    materialization_error: type[ValueError]


class SkillRunAdmissionService:
    """Resolve current dependencies and lock one immutable Skill set per Run."""

    def __init__(self, ports: SkillRunAdmissionPorts) -> None:
        self._ports = ports

    async def admit(
        self,
        conn: Any,
        *,
        skill: dict[str, Any],
        skill_id: str,
        input_payload: dict[str, Any],
        tenant_id: str,
        rollout_key: str,
        expected_version: str | None = None,
        department_id: str = "",
        roles: list[str] | None = None,
        permissions: list[str] | None = None,
    ) -> SkillRunAdmission:
        admissions = await self.admit_set(
            conn,
            roots=[(skill_id, skill, expected_version)],
            input_payload=input_payload,
            tenant_id=tenant_id,
            rollout_key=rollout_key,
            department_id=department_id, roles=roles, permissions=permissions,
        )
        return admissions[0]

    async def admit_set(
        self,
        conn: Any,
        *,
        roots: Sequence[tuple[str, dict[str, Any], str | None]],
        input_payload: dict[str, Any],
        tenant_id: str,
        rollout_key: str,
        department_id: str = "",
        roles: list[str] | None = None,
        permissions: list[str] | None = None,
    ) -> list[SkillRunAdmission]:
        """Resolve a set of already-authorized roots against one current dependency graph."""

        del input_payload  # Dependency selection is declaration-driven, never caller-selected.
        root_inputs = list(roots)
        if not root_inputs:
            raise self._ports.materialization_error("skill_version_not_materializable")
        if len(root_inputs) > MAX_SKILL_RUN_MANIFESTS:
            raise self._ports.materialization_error("skill_version_not_materializable")

        root_versions: dict[str, dict[str, Any]] = {}
        root_decisions: dict[str, Any] = {}
        for skill_id, skill, expected_version in root_inputs:
            if (
                not is_valid_executable_skill_name(skill_id)
                or skill_id in root_versions
            ):
                raise self._ports.materialization_error("skill_version_not_materializable")
            decision = self._ports.resolve_release_decision(
                skill,
                tenant_id=tenant_id,
                skill_id=skill_id,
                rollout_key=rollout_key,
            )
            selected_version = str(decision.selected_version or "")
            version = await self._load_exact_version(
                conn,
                skill_id=skill_id,
                version=selected_version,
            )
            root_versions[skill_id] = version
            root_decisions[skill_id] = decision

        versions_by_id = dict(root_versions)
        dependencies_by_id: dict[str, list[str]] = {
            skill_id: self._declared_dependencies(version)
            for skill_id, version in versions_by_id.items()
        }
        frontier = sorted(
            {
                dependency_id
                for dependency_ids in dependencies_by_id.values()
                for dependency_id in dependency_ids
                if dependency_id not in versions_by_id
            }
        )

        while frontier:
            if len(versions_by_id) + len(frontier) > MAX_SKILL_RUN_MANIFESTS:
                raise self._ports.materialization_error("skill_version_not_materializable")
            rows = await self._ports.catalog.list_public_skill_catalog(
                conn,
                tenant_id=tenant_id,
                include_disabled=True,
                rollout_key=rollout_key,
                skill_ids=frontier,
            )
            rows_by_id: dict[str, dict[str, Any]] = {}
            for row in rows:
                dependency_id = str(row.get("skill_id") or "")
                if not dependency_id or dependency_id in rows_by_id:
                    raise self._ports.materialization_error("skill_version_not_materializable")
                rows_by_id[dependency_id] = row

            next_frontier: set[str] = set()
            for dependency_id in frontier:
                row = rows_by_id.get(dependency_id)
                if row is None or not self._ports.dependency_is_usable(
                    tenant_id=tenant_id,
                    skill_id=dependency_id,
                    row=row,
                    department_id=department_id, roles=roles, permissions=permissions,
                ):
                    raise self._ports.materialization_error("skill_version_not_materializable")
                selected_version = str(row.get("version") or "")
                if (
                    not selected_version
                    or str(row.get("expected_version") or "") != selected_version
                ):
                    raise self._ports.materialization_error("skill_version_not_materializable")
                version = await self._load_exact_version(
                    conn,
                    skill_id=dependency_id,
                    version=selected_version,
                )
                if self._declared_dependencies(version) != self._declared_dependencies(row):
                    raise self._ports.materialization_error("skill_version_not_materializable")
                versions_by_id[dependency_id] = version
                dependency_ids = self._declared_dependencies(version)
                dependencies_by_id[dependency_id] = dependency_ids
                next_frontier.update(
                    item for item in dependency_ids if item not in versions_by_id
                )
                if len(set(versions_by_id) | next_frontier) > MAX_SKILL_RUN_MANIFESTS:
                    raise self._ports.materialization_error("skill_version_not_materializable")
            frontier = sorted(next_frontier - set(versions_by_id))

        self._validate_acyclic_complete_graph(dependencies_by_id)
        available_skill_ids = set(versions_by_id)
        manifests_by_id: dict[str, dict[str, Any]] = {}
        for skill_id, version in versions_by_id.items():
            manifests = self._ports.build_manifest_pins(
                version,
                available_skill_ids=available_skill_ids,
            )
            if (
                len(manifests) != 1
                or str(manifests[0].get("skill_id") or "") != skill_id
            ):
                raise self._ports.materialization_error("skill_version_not_materializable")
            manifests_by_id[skill_id] = manifests[0]

        admissions: list[SkillRunAdmission] = []
        for skill_id, _skill, expected_version in root_inputs:
            closure_ids = self._closure_order(skill_id, dependencies_by_id)
            closure_manifests = [manifests_by_id[item] for item in closure_ids]
            decision = root_decisions[skill_id]
            policy_version = decision.selected_version if decision.policy_active else None
            locked_version = self._ports.lock_skill_version(
                skill_id=skill_id,
                skill_manifests=closure_manifests,
                fallback_version=str(decision.selected_version or ""),
                release_policy_version=policy_version,
            )
            if expected_version is not None and locked_version != expected_version:
                raise SkillRunVersionMismatch("skill_run_version_mismatch")
            release_decision = self._ports.release_decision_payload(
                decision,
                locked_version=locked_version,
            )
            governed_manifests = self._ports.attach_snapshot_governance(
                closure_manifests,
                release_decision=release_decision,
            )
            admissions.append(
                SkillRunAdmission(governed_manifests, locked_version, release_decision)
            )
        return admissions

    async def _load_exact_version(
        self,
        conn: Any,
        *,
        skill_id: str,
        version: str,
    ) -> dict[str, Any]:
        if not is_valid_executable_skill_name(skill_id) or not version:
            raise self._ports.materialization_error("skill_version_not_materializable")
        skill_version = await self._ports.versions.get_effective_skill_version_for_policy(
            conn,
            skill_id=skill_id,
            version=version,
        )
        if (
            skill_version is None
            or str(skill_version.get("skill_id") or "") != skill_id
            or str(skill_version.get("version") or "") != version
            or str(skill_version.get("content_hash") or "") != version
            or not self._ports.is_user_runnable_status(skill_version.get("status"))
        ):
            raise self._ports.materialization_error("skill_version_not_materializable")
        return skill_version

    def _declared_dependencies(self, version: dict[str, Any]) -> list[str]:
        dependencies = version.get("dependency_ids")
        if (
            not isinstance(dependencies, list)
            or any(
                not is_valid_executable_skill_name(item) for item in dependencies
            )
            or len(dependencies) != len(set(dependencies))
        ):
            raise self._ports.materialization_error("skill_version_not_materializable")
        return list(dependencies)

    def _validate_acyclic_complete_graph(
        self,
        dependencies_by_id: dict[str, list[str]],
    ) -> None:
        states: dict[str, int] = {}

        def visit(skill_id: str) -> None:
            state = states.get(skill_id, 0)
            if state == 1 or skill_id not in dependencies_by_id:
                raise self._ports.materialization_error("skill_version_not_materializable")
            if state == 2:
                return
            states[skill_id] = 1
            for dependency_id in dependencies_by_id[skill_id]:
                visit(dependency_id)
            states[skill_id] = 2

        for skill_id in dependencies_by_id:
            visit(skill_id)

    def _closure_order(
        self,
        root_skill_id: str,
        dependencies_by_id: dict[str, list[str]],
    ) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()

        def add(skill_id: str) -> None:
            if skill_id in seen:
                return
            seen.add(skill_id)
            ordered.append(skill_id)
            for dependency_id in dependencies_by_id[skill_id]:
                add(dependency_id)

        add(root_skill_id)
        return ordered
