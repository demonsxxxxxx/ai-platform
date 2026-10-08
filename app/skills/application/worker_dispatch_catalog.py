"""Skills-owned Worker catalog binding and authorized input projection."""

from collections.abc import Awaitable, Callable
from typing import Any

from app.control_plane_contracts import RUN_EXECUTION_KIND_HARNESS_CHAT


async def materialize_worker_locked_skill_snapshots(
    conn: Any, *, tenant_id: str, run_id: str,
    skill_manifest_refs: Any,
    materialize: Callable[..., Awaitable[Any]],
    conflict_error: type[Exception],
) -> list[dict[str, Any]] | None:
    try:
        return await materialize(
            conn, tenant_id=tenant_id, run_id=run_id,
            skill_manifest_refs=skill_manifest_refs,
        )
    except conflict_error:
        return None


def resolve_worker_runtime_catalog(
    payload: Any, *, catalog_binding_type: Callable[..., Any],
    catalog_error_type: type[Exception], load_catalog: Callable[..., Any],
) -> Any | None:
    if payload.execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT:
        return None
    if payload.skill_id is None:
        raise catalog_error_type("authorized_skill_catalog_binding_invalid")
    binding = catalog_binding_type(
        tenant_id=payload.tenant_id, workspace_id=payload.workspace_id,
        user_id=payload.user_id, session_id=payload.session_id,
        run_id=payload.run_id, agent_id=payload.agent_id,
        selected_skill_id=payload.skill_id,
    )
    return load_catalog(
        payload.input, expected_binding=binding,
        pinned_manifests=payload.skill_manifests,
    )


def worker_catalog_public_metadata(
    catalog: Any | None,
) -> dict[str, dict[str, str]]:
    if catalog is None:
        return {}
    return {
        entry.skill_id: {
            "name": entry.name, "version": entry.version,
            "availability": entry.availability,
        }
        for entry in catalog.snapshot.entries
    }


def worker_pinned_manifests(payload: Any) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("skill_id")).strip(): item
        for item in payload.skill_manifests
        if isinstance(item, dict) and str(item.get("skill_id") or "").strip()
    }


def merged_worker_pinned_manifests(
    payload: Any, catalog: Any | None, *, catalog_error_type: type[Exception],
) -> dict[str, dict[str, Any]]:
    pinned = worker_pinned_manifests(payload)
    if catalog is None:
        return pinned
    for manifest in catalog.manifests:
        skill_id = str(manifest.get("skill_id") or "")
        existing = pinned.get(skill_id)
        if existing is not None:
            existing_version = str(existing.get("content_hash") or existing.get("version") or "")
            runtime_version = str(manifest.get("content_hash") or manifest.get("version") or "")
            if existing_version != runtime_version:
                raise catalog_error_type("authorized_skill_catalog_pin_mismatch")
            continue
        pinned[skill_id] = manifest
    return pinned


def worker_catalog_binding(
    run_identity: dict[str, str], *, binding_type: Callable[..., Any],
) -> Any:
    return binding_type(
        tenant_id=run_identity["tenant_id"],
        workspace_id=run_identity["workspace_id"],
        user_id=run_identity["user_id"],
        session_id=run_identity["session_id"],
        run_id=run_identity["run_id"],
        agent_id=run_identity["agent_id"],
        selected_skill_id=run_identity["skill_id"],
    )


def worker_payload_with_authorized_catalog(
    payload: Any, *, resolution: Any, catalog_key: str, manifests_key: str,
) -> Any:
    rebuilt_input = dict(payload.input)
    rebuilt_input.pop(catalog_key, None)
    rebuilt_input.pop(manifests_key, None)
    rebuilt_input.update(
        resolution.runtime_input_updates(pinned_manifests=payload.skill_manifests)
    )
    return payload.model_copy(update={"input": rebuilt_input})
