from copy import deepcopy

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import transaction
from app.models import (
    AdminSkillDetailResponse,
    AdminSkillPromoteRequest,
    AdminSkillReleasePolicyResponse,
    AdminSkillRollbackRequest,
    AdminSkillSyncResponse,
    AdminSkillUploadResponse,
    AdminSkillVersionDiffResponse,
)
from app.settings import get_settings
from app.skills.packages import MAX_SKILL_PACKAGE_TOTAL_BYTES, parse_skill_package_zip
from app.skills.dependencies import SkillDependencyPolicyError, skill_dependency_ids, skill_dependency_policy
from app.skills.pinning import (
    SkillVersionMaterializationError,
    build_skill_manifest_pins,
    build_skill_version_dependency_manifest_pins,
    build_skill_version_manifest_pin,
    validate_skill_version_dependency_policy,
)
from app.skills.registry import BuiltinSkillRegistry
from app.storage import ObjectStorage
from app.validation import assert_safe_id

router = APIRouter()
UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


def _require_admin(principal: AuthPrincipal) -> None:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")


def _safe_skill_id(skill_id: str) -> str:
    try:
        return assert_safe_id(skill_id, "skill_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _safe_version(value: str, field_name: str) -> str:
    try:
        return assert_safe_id(value, field_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _current_builtin_skill_version(skill_id: str) -> str | None:
    registry = BuiltinSkillRegistry(get_settings().platform_skills_root)
    pins = build_skill_manifest_pins(
        skill_id=skill_id,
        input_payload={},
        builtin_skills=registry.list_builtin_skills(),
    )
    for pin in pins:
        if str(pin.get("skill_id") or "") == skill_id:
            return str(pin.get("content_hash") or pin.get("version") or "") or None
    return None


async def _read_skill_package_upload(package: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        chunk = await package.read(UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > MAX_SKILL_PACKAGE_TOTAL_BYTES:
            raise HTTPException(status_code=400, detail="skill_package_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _require_active_skill_version(version: dict[str, object]) -> None:
    if str(version.get("status") or "") != "active":
        raise HTTPException(status_code=409, detail="skill_version_inactive")


def _available_builtin_skill_ids_or_409() -> set[str]:
    try:
        return {skill.name for skill in BuiltinSkillRegistry(get_settings().platform_skills_root).list_builtin_skills()}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="skill_version_not_materializable") from exc


def _require_materializable_skill_version(skill_id: str, version: dict[str, object]) -> None:
    source = version.get("source")
    if not isinstance(source, dict):
        raise HTTPException(status_code=409, detail="skill_version_not_materializable")
    try:
        build_skill_version_manifest_pin(version)
        validate_skill_version_dependency_policy(
            version,
            available_skill_ids=_available_builtin_skill_ids_or_409(),
        )
        build_skill_version_dependency_manifest_pins(version)
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail="skill_version_not_materializable") from exc


def _skill_dependency_ids_or_409(skill_id: str, available_skill_ids: set[str]) -> list[str]:
    try:
        return skill_dependency_ids(skill_id, available_skill_ids)
    except SkillDependencyPolicyError as exc:
        raise HTTPException(status_code=409, detail="skill_dependency_policy_violation") from exc


def _dependency_manifest_snapshots_or_409(
    dependency_ids: list[str],
    manifest_by_skill_id: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    for dependency_id in dependency_ids:
        manifest = manifest_by_skill_id.get(dependency_id)
        if manifest is None:
            raise HTTPException(status_code=409, detail="skill_version_not_materializable")
        snapshots.append(deepcopy(manifest))
    return snapshots


def _builtin_dependency_manifest_snapshots(dependency_ids: list[str]) -> list[dict[str, object]]:
    if not dependency_ids:
        return []
    registry = BuiltinSkillRegistry(get_settings().platform_skills_root)
    try:
        dependency_pins = build_skill_manifest_pins(
            skill_id="",
            input_payload={"skill_ids": dependency_ids},
            builtin_skills=registry.list_builtin_skills(),
        )
    except SkillDependencyPolicyError as exc:
        raise HTTPException(status_code=409, detail="skill_dependency_policy_violation") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="skill_version_not_materializable") from exc
    manifest_by_skill_id = {str(item.get("skill_id") or ""): item for item in dependency_pins}
    return _dependency_manifest_snapshots_or_409(dependency_ids, manifest_by_skill_id)


@router.get("/admin/skills/{skill_id}", response_model=AdminSkillDetailResponse)
async def admin_skill_detail(
    skill_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> AdminSkillDetailResponse:
    _require_admin(principal)
    skill_id = _safe_skill_id(skill_id)

    async with transaction() as conn:
        detail = await repositories.get_admin_skill_detail(
            conn,
            tenant_id=principal.tenant_id,
            skill_id=skill_id,
        )
        if detail is not None:
            available_skill_ids = set(await repositories.list_skill_ids(conn))
    if detail is None:
        raise HTTPException(status_code=404, detail="skill_not_found")
    detail = {
        **detail,
        "dependency_policy": skill_dependency_policy(skill_id, available_skill_ids),
    }
    return AdminSkillDetailResponse.model_validate(detail)


@router.post("/admin/skills/sync-builtin", response_model=AdminSkillSyncResponse)
async def admin_sync_builtin_skills(
    principal: AuthPrincipal = Depends(require_principal),
) -> AdminSkillSyncResponse:
    _require_admin(principal)

    registry = BuiltinSkillRegistry(get_settings().platform_skills_root)
    builtins = registry.list_builtin_skills()
    available_skill_ids = {skill.name for skill in builtins}
    try:
        manifest_pins = build_skill_manifest_pins(
            skill_id="",
            input_payload={"skill_ids": [skill.name for skill in builtins]},
            builtin_skills=builtins,
        )
    except SkillDependencyPolicyError as exc:
        raise HTTPException(status_code=409, detail="skill_dependency_policy_violation") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="skill_version_not_materializable") from exc
    manifest_by_skill_id = {str(item.get("skill_id") or ""): item for item in manifest_pins}
    synced = []
    async with transaction() as conn:
        for skill in builtins:
            dependency_ids = _skill_dependency_ids_or_409(skill.name, available_skill_ids)
            manifest = manifest_by_skill_id.get(skill.name)
            if manifest is None:
                raise HTTPException(status_code=409, detail="skill_version_not_materializable")
            source_json = dict(skill.source)
            source_json["files"] = list(manifest.get("files") or [])
            if dependency_ids:
                source_json["dependency_manifests"] = _dependency_manifest_snapshots_or_409(
                    dependency_ids,
                    manifest_by_skill_id,
                )
            await repositories.upsert_skill_version(
                conn,
                skill_id=skill.name,
                version=skill.version,
                content_hash=skill.version,
                description=skill.description,
                source_json=source_json,
                dependency_ids=dependency_ids,
                status="active",
                created_by=principal.user_id,
            )
            await repositories.backfill_builtin_skill_version_snapshot(
                conn,
                skill_id=skill.name,
                version=skill.version,
                source_json=source_json,
                dependency_ids=dependency_ids,
                description=skill.description,
            )
            await repositories.update_skill_catalog_version(
                conn,
                skill_id=skill.name,
                version=skill.version,
                description=skill.description,
            )
            synced.append(
                {
                    "skill_id": skill.name,
                    "version": skill.version,
                    "content_hash": skill.version,
                    "description": skill.description,
                    "source": source_json,
                    "dependency_ids": dependency_ids,
                    "status": "active",
                    "created_by": principal.user_id,
                    "created_at": None,
                }
            )
    return AdminSkillSyncResponse(synced=synced)


@router.post("/admin/skills/{skill_id}/versions/upload", response_model=AdminSkillUploadResponse)
async def admin_upload_skill_package(
    skill_id: str,
    package: UploadFile = File(...),
    principal: AuthPrincipal = Depends(require_principal),
) -> AdminSkillUploadResponse:
    _require_admin(principal)
    skill_id = _safe_skill_id(skill_id)

    async with transaction() as conn:
        skill = await repositories.get_skill(conn, skill_id=skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="skill_not_found")
        if str(skill.get("status") or "") != "active":
            raise HTTPException(status_code=409, detail="skill_inactive")
        available_skill_ids = set(await repositories.list_skill_ids(conn))
        dependency_ids = _skill_dependency_ids_or_409(skill_id, available_skill_ids)

    package_content = await _read_skill_package_upload(package)
    try:
        parsed = parse_skill_package_zip(package_content, expected_skill_id=skill_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with transaction() as conn:
        existing = await repositories.get_skill_version(conn, skill_id=skill_id, version=parsed.content_hash)
        if existing is not None:
            _require_materializable_skill_version(skill_id, existing)
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="skill_version_upload_reused",
                target_type="skill",
                target_id=skill_id,
                payload_json={
                    "skill_id": skill_id,
                    "version": parsed.content_hash,
                    "storage_key": (existing.get("source") or {}).get("storage_key")
                    if isinstance(existing.get("source"), dict)
                    else None,
                },
            )
            return AdminSkillUploadResponse(uploaded=existing)

    dependency_manifests = _builtin_dependency_manifest_snapshots(dependency_ids)
    storage_key = f"skills/{skill_id}/versions/{parsed.content_hash}/package.zip"
    stored = ObjectStorage().put_bytes(
        storage_key=storage_key,
        content=package_content,
        content_type="application/zip",
    )
    source_json = {
        "kind": "uploaded",
        "storage_key": stored.storage_key,
        "package_sha256": stored.sha256,
        "size_bytes": stored.size_bytes,
        "files": parsed.files,
    }
    if dependency_manifests:
        source_json["dependency_manifests"] = dependency_manifests
    uploaded = {
        "skill_id": skill_id,
        "version": parsed.content_hash,
        "content_hash": parsed.content_hash,
        "description": parsed.description,
        "source": source_json,
        "dependency_ids": dependency_ids,
        "status": "active",
        "created_by": principal.user_id,
        "created_at": None,
    }
    async with transaction() as conn:
        await repositories.upsert_skill_version(
            conn,
            skill_id=skill_id,
            version=parsed.content_hash,
            content_hash=parsed.content_hash,
            description=parsed.description,
            source_json=source_json,
            dependency_ids=dependency_ids,
            status="active",
            created_by=principal.user_id,
        )
        await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="skill_version_uploaded",
            target_type="skill",
            target_id=skill_id,
            payload_json={
                "skill_id": skill_id,
                "version": parsed.content_hash,
                "storage_key": stored.storage_key,
                "package_sha256": stored.sha256,
                "size_bytes": stored.size_bytes,
            },
        )
    return AdminSkillUploadResponse(uploaded=uploaded)


@router.get("/admin/skills/{skill_id}/versions/diff", response_model=AdminSkillVersionDiffResponse)
async def admin_skill_version_diff(
    skill_id: str,
    from_version: str = Query(...),
    to_version: str = Query(...),
    principal: AuthPrincipal = Depends(require_principal),
) -> AdminSkillVersionDiffResponse:
    _require_admin(principal)
    skill_id = _safe_skill_id(skill_id)
    from_version = _safe_version(from_version, "from_version")
    to_version = _safe_version(to_version, "to_version")
    try:
        async with transaction() as conn:
            diff = await repositories.diff_skill_versions(
                conn,
                skill_id=skill_id,
                from_version=from_version,
                to_version=to_version,
            )
    except repositories.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AdminSkillVersionDiffResponse.model_validate(diff)


@router.post("/admin/skills/{skill_id}/promote", response_model=AdminSkillReleasePolicyResponse)
async def admin_promote_skill_version(
    skill_id: str,
    request: AdminSkillPromoteRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> AdminSkillReleasePolicyResponse:
    _require_admin(principal)
    skill_id = _safe_skill_id(skill_id)
    async with transaction() as conn:
        version = await repositories.get_skill_version(conn, skill_id=skill_id, version=request.version)
        if version is None:
            raise HTTPException(status_code=404, detail="skill_version_not_found")
        _require_active_skill_version(version)
        _require_materializable_skill_version(skill_id, version)
        policy = await repositories.get_skill_release_policy(
            conn,
            tenant_id=principal.tenant_id,
            skill_id=skill_id,
            channel=request.channel,
        )
        previous_version = policy["current_version"] if policy else None
        if policy is None:
            skill = await repositories.get_skill(conn, skill_id=skill_id)
            if skill is None:
                raise HTTPException(status_code=404, detail="skill_not_found")
            previous_version = str(skill.get("version") or "") or None
            if request.rollout_percent < 100 and previous_version:
                previous = version if previous_version == request.version else await repositories.get_skill_version(
                    conn,
                    skill_id=skill_id,
                    version=previous_version,
                )
                if previous is None:
                    raise HTTPException(status_code=409, detail="skill_version_not_materializable")
                _require_active_skill_version(previous)
                _require_materializable_skill_version(skill_id, previous)
        elif request.rollout_percent < 100 and previous_version:
            previous = version if previous_version == request.version else await repositories.get_skill_version(
                conn,
                skill_id=skill_id,
                version=str(previous_version),
            )
            if previous is None:
                raise HTTPException(status_code=409, detail="skill_version_not_materializable")
            _require_active_skill_version(previous)
            _require_materializable_skill_version(skill_id, previous)
        await repositories.set_skill_release_policy(
            conn,
            tenant_id=principal.tenant_id,
            skill_id=skill_id,
            version=request.version,
            previous_version=previous_version,
            promoted_by=principal.user_id,
            channel=request.channel,
            rollout_percent=request.rollout_percent,
        )
        await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="skill_version_promoted",
            target_type="skill",
            target_id=skill_id,
            payload_json={
                "skill_id": skill_id,
                "from_version": previous_version,
                "to_version": request.version,
                "channel": request.channel,
                "rollout_percent": request.rollout_percent,
            },
        )
    return AdminSkillReleasePolicyResponse(
        skill_id=skill_id,
        channel=request.channel,
        current_version=request.version,
        previous_version=previous_version,
        rollout_percent=request.rollout_percent,
        status="active",
    )


@router.post("/admin/skills/{skill_id}/rollback", response_model=AdminSkillReleasePolicyResponse)
async def admin_rollback_skill_version(
    skill_id: str,
    request: AdminSkillRollbackRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> AdminSkillReleasePolicyResponse:
    _require_admin(principal)
    skill_id = _safe_skill_id(skill_id)
    async with transaction() as conn:
        version = await repositories.get_skill_version(conn, skill_id=skill_id, version=request.version)
        if version is None:
            raise HTTPException(status_code=404, detail="skill_version_not_found")
        _require_active_skill_version(version)
        _require_materializable_skill_version(skill_id, version)
        policy = await repositories.get_skill_release_policy(
            conn,
            tenant_id=principal.tenant_id,
            skill_id=skill_id,
            channel=request.channel,
        )
        if policy is None:
            raise HTTPException(status_code=409, detail="rollback_policy_not_available")
        policy_current_version = str(policy["current_version"])
        policy_previous_version = policy.get("previous_version")
        if policy_previous_version:
            if request.version != policy_previous_version:
                raise HTTPException(status_code=409, detail="rollback_target_not_previous_version")
            previous_version = policy_current_version
        else:
            if request.version != policy_current_version:
                raise HTTPException(status_code=409, detail="rollback_target_not_previous_version")
            previous_version = None
        if request.version == policy_current_version and policy_previous_version:
            raise HTTPException(status_code=409, detail="rollback_target_not_previous_version")
        await repositories.set_skill_release_policy(
            conn,
            tenant_id=principal.tenant_id,
            skill_id=skill_id,
            version=request.version,
            previous_version=previous_version,
            promoted_by=principal.user_id,
            channel=request.channel,
            rollout_percent=100,
        )
        await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="skill_version_rolled_back",
            target_type="skill",
            target_id=skill_id,
            payload_json={
                "skill_id": skill_id,
                "from_version": previous_version,
                "to_version": request.version,
                "channel": request.channel,
                "rollout_percent": 100,
            },
        )
    return AdminSkillReleasePolicyResponse(
        skill_id=skill_id,
        channel=request.channel,
        current_version=request.version,
        previous_version=previous_version,
        rollout_percent=100,
        status="active",
    )
