"""Skills-owned dispatch reauthorization for immutable pins and current catalog."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerSkillCandidate:
    skill: dict[str, Any]
    lifecycle_status: str
    profile_skill_set: list[Any] | None
    pinned_mcp_tool_ids: list[str]
    denial_reason: str | None = None


@dataclass(frozen=True)
class WorkerSkillCatalog:
    payload: Any
    manifests: list[dict[str, Any]]
    skill_names: list[str]
    denial_reason: str | None = None


class WorkerSkillDispatchAuthorization:
    def __init__(
        self, *, validate_snapshots: Callable[..., Awaitable[Any]],
        validate_replay: Callable[..., Awaitable[Any]],
        resolve_skill: Callable[..., Awaitable[dict[str, Any]]],
        resolve_catalog: Callable[..., Awaitable[Any]],
        catalog_binding: Callable[..., Any],
        attach_catalog: Callable[..., Any],
        conflict_error: type[Exception],
        authorization_error: type[Exception],
        not_found_error: type[Exception],
        catalog_error: type[Exception],
        synthetic_skill_id: str,
    ) -> None:
        self._validate_snapshots = validate_snapshots
        self._validate_replay = validate_replay
        self._resolve_skill = resolve_skill
        self._resolve_catalog = resolve_catalog
        self._catalog_binding = catalog_binding
        self._attach_catalog = attach_catalog
        self._conflict_error = conflict_error
        self._authorization_error = authorization_error
        self._not_found_error = not_found_error
        self._catalog_error = catalog_error
        self._synthetic_skill_id = synthetic_skill_id

    async def candidate(
        self, conn: Any, *, payload: Any, run_identity: dict[str, str],
    ) -> WorkerSkillCandidate:
        try:
            await self._validate_snapshots(
                conn, tenant_id=run_identity["tenant_id"], run_id=run_identity["run_id"],
                skill_manifests=payload.skill_manifests, release_decision=payload.release_decision,
            )
        except self._conflict_error:
            return WorkerSkillCandidate({}, "disabled", None, [], "skill_snapshot_identity_mismatch")
        profile_skill_set = (
            payload.agent_profile.get("skill_set") if isinstance(payload.agent_profile, dict) else None
        )
        try:
            pinned_mcp_tool_ids = await self._validate_replay(
                conn, skill_id=run_identity["skill_id"],
                pinned_version=str(payload.skill_version or ""),
                pinned_executor_type=payload.executor_type,
                skill_manifests=payload.skill_manifests,
                skill_set=profile_skill_set if isinstance(profile_skill_set, list) else None,
            )
        except (self._authorization_error, self._conflict_error):
            return WorkerSkillCandidate({}, "disabled", None, [], "skill_historical_pin_revoked")
        skill: dict[str, Any] = {}
        lifecycle_status = "disabled"
        try:
            skill = await self._resolve_skill(
                conn, tenant_id=run_identity["tenant_id"],
                agent_id=run_identity["agent_id"], skill_id=run_identity["skill_id"],
            )
            lifecycle_status = str(skill.get("skill_status") or "disabled")
        except (self._not_found_error, self._conflict_error):
            pass
        return WorkerSkillCandidate(
            skill, lifecycle_status,
            profile_skill_set if isinstance(profile_skill_set, list) else None,
            list(pinned_mcp_tool_ids or []),
        )

    async def catalog(
        self, conn: Any, *, payload: Any, run_identity: dict[str, str],
        principal: Any, profile_skill_set: list[Any] | None,
    ) -> WorkerSkillCatalog:
        if payload.executor_type != "claude-agent-worker":
            return WorkerSkillCatalog(payload, [], [run_identity["skill_id"]])
        try:
            authorized = await self._resolve_catalog(
                conn, binding=self._catalog_binding(run_identity),
                department_id=principal.department_id, roles=principal.roles,
                permissions=principal.permissions,
                pinned_manifests=payload.skill_manifests,
                skill_set=profile_skill_set,
            )
        except (self._catalog_error, self._conflict_error):
            return WorkerSkillCatalog(payload, [], [], "authorized_skill_catalog_unavailable")
        entry = authorized.snapshot.entry(run_identity["skill_id"])
        if run_identity["skill_id"] != self._synthetic_skill_id and (
            entry is None or not entry.available
        ):
            return WorkerSkillCatalog(payload, [], [], "selected_skill_catalog_unavailable")
        return WorkerSkillCatalog(
            self._attach_catalog(payload, resolution=authorized),
            authorized.manifests,
            list(authorized.snapshot.materialized_skill_ids),
        )
