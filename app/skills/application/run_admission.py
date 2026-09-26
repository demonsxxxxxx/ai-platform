"""Application orchestration for immutable Skill run admission."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


class SkillRunVersionMismatch(ValueError):
    """The materialized Skill differs from the caller's admitted version."""


@dataclass(frozen=True)
class SkillRunAdmission:
    skill_manifests: list[dict[str, Any]]
    skill_version: str
    release_decision: dict[str, Any]


class SkillCatalog(Protocol):
    async def get_skill(self, conn: Any, *, skill_id: str) -> dict[str, Any] | None: ...


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
    build_manifest_pins: Callable[..., list[dict[str, Any]]]
    lock_skill_version: Callable[..., str]
    attach_snapshot_governance: Callable[..., list[dict[str, Any]]]
    materialization_error: type[ValueError]


class SkillRunAdmissionService:
    """Resolve policy, materialize manifests, and lock one immutable Skill version."""

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
    ) -> SkillRunAdmission:
        decision = self._ports.resolve_release_decision(
            skill,
            tenant_id=tenant_id,
            skill_id=skill_id,
            rollout_key=rollout_key,
        )
        policy_version = decision.selected_version if decision.policy_active else None
        manifests = await self._materialize_manifest_pins(
            conn,
            skill_id=skill_id,
            input_payload=input_payload,
            release_policy_version=policy_version,
        )
        locked_version = self._ports.lock_skill_version(
            skill_id=skill_id,
            skill_manifests=manifests,
            fallback_version=decision.selected_version,
            release_policy_version=policy_version,
        )
        if expected_version is not None and locked_version != expected_version:
            raise SkillRunVersionMismatch("skill_run_version_mismatch")
        release_decision = self._ports.release_decision_payload(
            decision,
            locked_version=locked_version,
        )
        manifests = self._ports.attach_snapshot_governance(
            manifests,
            release_decision=release_decision,
        )
        return SkillRunAdmission(manifests, locked_version, release_decision)

    async def _materialize_manifest_pins(
        self,
        conn: Any,
        *,
        skill_id: str,
        input_payload: dict[str, Any],
        release_policy_version: object | None,
    ) -> list[dict[str, Any]]:
        def available_skill_ids(
            root_skill_id: str,
            skill_version: dict[str, Any],
            requested_ids: set[str] | None = None,
        ) -> set[str]:
            available = set(requested_ids or ()) | {root_skill_id}
            dependency_ids = skill_version.get("dependency_ids")
            if isinstance(dependency_ids, list):
                available.update(
                    item for item in dependency_ids if isinstance(item, str)
                )
            source = skill_version.get("source")
            dependency_manifests = (
                source.get("dependency_manifests") if isinstance(source, dict) else None
            )
            if isinstance(dependency_manifests, list):
                available.update(
                    str(item.get("skill_id"))
                    for item in dependency_manifests
                    if isinstance(item, dict) and item.get("skill_id")
                )
            return available

        policy_version = str(release_policy_version or "")
        if policy_version:
            version = await self._ports.versions.get_effective_skill_version_for_policy(
                conn,
                skill_id=skill_id,
                version=policy_version,
            )
            if version is None or not self._ports.is_user_runnable_status(
                version.get("status")
            ):
                raise self._ports.materialization_error(
                    "skill_version_not_materializable"
                )
            return self._ports.build_manifest_pins(
                version,
                available_skill_ids=available_skill_ids(skill_id, version),
            )

        requested_ids = [skill_id]
        raw_skill_ids = input_payload.get("skill_ids")
        if isinstance(raw_skill_ids, list):
            requested_ids.extend(
                item for item in raw_skill_ids if isinstance(item, str)
            )
        requested_ids = list(dict.fromkeys(item for item in requested_ids if item))
        requested_id_set = set(requested_ids)
        manifests_by_id: dict[str, dict[str, Any]] = {}
        for requested_id in requested_ids:
            skill_row = await self._ports.catalog.get_skill(conn, skill_id=requested_id)
            version = str((skill_row or {}).get("version") or "")
            if not version:
                raise self._ports.materialization_error(
                    "skill_version_not_materializable"
                )
            skill_version = (
                await self._ports.versions.get_effective_skill_version_for_policy(
                    conn,
                    skill_id=requested_id,
                    version=version,
                )
            )
            if skill_version is None or not self._ports.is_user_runnable_status(
                skill_version.get("status")
            ):
                raise self._ports.materialization_error(
                    "skill_version_not_materializable"
                )
            for manifest in self._ports.build_manifest_pins(
                skill_version,
                available_skill_ids=available_skill_ids(
                    requested_id,
                    skill_version,
                    requested_id_set,
                ),
            ):
                manifest_id = str(manifest.get("skill_id") or "")
                existing = manifests_by_id.get(manifest_id)
                if existing is not None and existing.get(
                    "content_hash"
                ) != manifest.get("content_hash"):
                    raise self._ports.materialization_error(
                        "skill_version_not_materializable"
                    )
                manifests_by_id.setdefault(manifest_id, manifest)
        return list(manifests_by_id.values())
