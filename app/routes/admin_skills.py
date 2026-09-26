from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import transaction
from app.identity.infrastructure import audit_postgres as identity_audit
from app.models import (
    AdminSkillDetailResponse,
    AdminSkillPromoteRequest,
    AdminSkillReleasePolicyResponse,
    AdminSkillRollbackRequest,
    AdminSkillUploadResponse,
    AdminSkillVersionDiffResponse,
    AdminSkillVersionResponse,
    AdminSkillVersionStatusRequest,
    PublicSkillImportPreviewResponse,
)
from app.platform.postgres import errors as platform_errors
from app.skills.api import (
    INTERNAL_DEPENDENCY_SKILL_IDS,
    AdminSkillListResponse,
    list_uploaded_skill_display_version_rows,
    lock_skill_for_version_upload,
    next_uploaded_skill_display_version,
    resolve_uploaded_skill_display_versions,
)
from app.skills.dependencies import skill_dependency_policy
from app.skills.infrastructure import catalog_postgres as skills_catalog
from app.skills.infrastructure import postgres as skills_postgres
from app.skills.infrastructure import versions_postgres as skills_versions
from app.skills.lifecycle import (
    SKILL_VERSION_DEPRECATED,
    SKILL_VERSION_DISABLED,
    SKILL_VERSION_DRAFT,
    SKILL_VERSION_RELEASED,
    SKILL_VERSION_REVIEWED,
    is_releasable_status,
    normalize_skill_version_status,
)
from app.skills.packages import (
    MAX_SKILL_PACKAGE_TOTAL_BYTES,
    build_skill_dependency_evidence,
    build_skill_package_contract,
    build_uploaded_skill_admin_trust_review,
    parse_skill_package_zip,
    validate_skill_package_contract,
)
from app.skills.pinning import (
    SkillVersionMaterializationError,
    build_skill_version_dependency_manifest_pins,
    build_skill_version_manifest_pin,
    validate_skill_version_dependency_policy,
)
from app.skills.release_readiness import build_skill_version_release_review
from app.storage import (
    ObjectStorage,
    StorageIOBusyError,
    StorageIOTimeoutError,
    run_storage_io,
)
from app.validation import assert_safe_id

router = APIRouter()
UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


def _require_admin(principal: AuthPrincipal) -> None:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")


def _require_skill_upload_admin(principal: AuthPrincipal) -> None:
    permissions = {item.strip().lower() for item in principal.permissions if item.strip()}
    if not is_ai_admin(principal) and "skill:admin" not in permissions:
        raise HTTPException(status_code=403, detail="not_ai_admin")


def _current_builtin_skill_version(skill_id: str) -> str | None:
    return None


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


def _require_releasable_skill_version(version: dict[str, object]) -> None:
    status = normalize_skill_version_status(version.get("status"))
    if is_releasable_status(status):
        return
    if status == SKILL_VERSION_DRAFT:
        raise HTTPException(status_code=409, detail="skill_version_not_reviewed")
    raise HTTPException(status_code=409, detail="skill_version_inactive")


def _require_rollback_target_skill_version(version: dict[str, object]) -> None:
    status = normalize_skill_version_status(version.get("status"))
    if is_releasable_status(status) or status == SKILL_VERSION_DEPRECATED:
        return
    if status == SKILL_VERSION_DRAFT:
        raise HTTPException(status_code=409, detail="skill_version_not_reviewed")
    raise HTTPException(status_code=409, detail="skill_version_inactive")


def _available_dependency_skill_ids(skill_id: str) -> set[str]:
    return {skill_id, *INTERNAL_DEPENDENCY_SKILL_IDS}


def _require_materializable_skill_version(skill_id: str, version: dict[str, object]) -> None:
    source = version.get("source")
    if not isinstance(source, dict):
        raise HTTPException(status_code=409, detail="skill_version_not_materializable")
    try:
        build_skill_version_manifest_pin(version)
        validate_skill_version_dependency_policy(
            version,
            available_skill_ids=_available_dependency_skill_ids(skill_id),
        )
        build_skill_version_dependency_manifest_pins(version)
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail="skill_version_not_materializable") from exc


def _require_rollback_materializable_skill_version(skill_id: str, version: dict[str, object]) -> None:
    materialization_version = version
    if normalize_skill_version_status(version.get("status")) == SKILL_VERSION_DEPRECATED:
        materialization_version = {**version, "status": SKILL_VERSION_RELEASED}
    _require_materializable_skill_version(skill_id, materialization_version)


def _require_reusable_uploaded_skill_version(skill_id: str, version: dict[str, object]) -> None:
    source = version.get("source")
    if not isinstance(source, dict) or source.get("kind") != "uploaded":
        raise HTTPException(status_code=409, detail="skill_version_not_materializable")
    package_contract = source.get("package_contract")
    if not isinstance(package_contract, dict):
        _require_materializable_skill_version(skill_id, version)
        return
    try:
        validate_skill_package_contract(
            package_contract,
            skill_id=skill_id,
            content_hash=str(version.get("content_hash") or ""),
        )
        validate_skill_version_dependency_policy(
            version,
            available_skill_ids=_available_dependency_skill_ids(skill_id),
        )
        build_skill_version_dependency_manifest_pins(version)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="skill_version_not_materializable") from exc
    except SkillVersionMaterializationError as exc:
        raise HTTPException(status_code=409, detail="skill_version_not_materializable") from exc


def _build_skill_version_admin_review(version: dict[str, object]) -> dict[str, object]:
    source = version.get("source") if isinstance(version.get("source"), dict) else {}
    if source.get("kind") == "uploaded":
        return build_uploaded_skill_admin_trust_review(version)
    return build_skill_version_release_review(version)


def _require_reviewed_skill_version_release(version: dict[str, object]) -> dict[str, object]:
    review = _build_skill_version_admin_review(version)
    if review.get("status") != "passed" or review.get("blockers"):
        raise HTTPException(status_code=409, detail="skill_release_review_not_verified")
    return review


def _release_review_summary(review: dict[str, object] | None) -> dict[str, object]:
    review = review or {}
    blockers = review.get("blockers")
    package_evidence = review.get("package_evidence") if isinstance(review.get("package_evidence"), dict) else {}
    release_review = review.get("release_review") if isinstance(review.get("release_review"), dict) else {}
    return {
        "schema_version": str(review.get("schema_version") or ""),
        "status": str(review.get("status") or ""),
        "trust_basis": str(review.get("trust_basis") or ""),
        "package_contract_valid": review.get("package_contract_valid") is True,
        "blocker_count": len(blockers) if isinstance(blockers, list) else 0,
        "dependency_evidence_present": bool(package_evidence)
        or any(
            bool(release_review.get(flag))
            for flag in ("sbom_reviewed", "license_policy_reviewed", "vulnerability_reviewed")
        ),
    }


def _release_policy_protects_version(policy: dict[str, object] | None, version: str) -> bool:
    if policy is None:
        return False
    if str(policy.get("current_version") or "") == version:
        return True
    raw_rollout_percent = policy.get("rollout_percent")
    try:
        rollout_percent = 100 if raw_rollout_percent is None else int(raw_rollout_percent)
    except (TypeError, ValueError):
        rollout_percent = 100
    return rollout_percent < 100 and str(policy.get("previous_version") or "") == version


async def _mark_skill_version_released(
    conn,
    *,
    skill_id: str,
    version: str,
) -> None:
    await skills_versions.update_skill_version_status(
        conn,
        skill_id=skill_id,
        version=version,
        status=SKILL_VERSION_RELEASED,
    )


async def _mark_superseded_skill_version_deprecated(
    conn,
    *,
    skill_id: str,
    version: str | None,
    target_version: str,
) -> str | None:
    if not version or version == target_version:
        return None
    previous = await skills_postgres.get_skill_version(conn, skill_id=skill_id, version=version)
    if previous is None:
        return None
    if normalize_skill_version_status(previous.get("status")) == SKILL_VERSION_DISABLED:
        return None
    await skills_versions.update_skill_version_status(
        conn,
        skill_id=skill_id,
        version=version,
        status=SKILL_VERSION_DEPRECATED,
    )
    return version


@router.get("/admin/skills", response_model=AdminSkillListResponse)
async def admin_list_skills(
    principal: AuthPrincipal = Depends(require_principal),
) -> AdminSkillListResponse:
    _require_admin(principal)
    async with transaction() as conn:
        items = await skills_versions.list_admin_skill_summaries(
            conn,
            tenant_id=principal.tenant_id,
        )
        display_rows = await list_uploaded_skill_display_version_rows(
            conn,
            skill_ids=[str(item.get("skill_id") or "") for item in items],
        )
    display_versions = resolve_uploaded_skill_display_versions(display_rows)
    uploaded_dates = {
        (str(row["skill_id"]), str(row["version"])): row["created_at"].isoformat()
        for row in display_rows
        if row.get("created_at") is not None
    }
    for item in items:
        skill_id = str(item.get("skill_id") or "")
        latest_version = item.get("latest_version")
        current_version = item.get("current_version")
        item["latest_display_version"] = display_versions.get(
            (skill_id, str(latest_version or ""))
        )
        item["current_display_version"] = display_versions.get(
            (skill_id, str(current_version or ""))
        )
        item["latest_uploaded_at"] = uploaded_dates.get(
            (skill_id, str(latest_version or ""))
        )
    items = [item for item in items if item.get("lifecycle_status") == "active"]
    return AdminSkillListResponse(items=items)


@router.get("/admin/skills/{skill_id}", response_model=AdminSkillDetailResponse)
async def admin_skill_detail(
    skill_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> AdminSkillDetailResponse:
    _require_admin(principal)
    skill_id = _safe_skill_id(skill_id)

    async with transaction() as conn:
        detail = await skills_versions.get_admin_skill_detail(
            conn,
            tenant_id=principal.tenant_id,
            skill_id=skill_id,
        )
        if (
            detail is not None
            and isinstance(detail.get("skill"), dict)
            and detail["skill"].get("lifecycle_status") == "active"
        ):
            available_skill_ids = set(await skills_catalog.list_skill_ids(conn))
        else:
            detail = None
    if detail is None:
        raise HTTPException(status_code=404, detail="skill_not_found")
    versions = detail.get("versions") if isinstance(detail.get("versions"), list) else []
    release_policy = detail.get("release_policy") if isinstance(detail.get("release_policy"), dict) else {}
    current_version = str(release_policy.get("current_version") or "")
    selected_version = next(
        (version for version in versions if str(version.get("version") or "") == current_version),
        versions[0] if versions else {},
    )
    dependency_ids = selected_version.get("dependency_ids")
    if not isinstance(dependency_ids, list) or not all(isinstance(item, str) for item in dependency_ids):
        dependency_ids = []
    detail = {
        **detail,
        "dependency_policy": skill_dependency_policy(skill_id, available_skill_ids, dependency_ids),
    }
    return AdminSkillDetailResponse.model_validate(detail)


@router.post("/admin/skills/{skill_id}/versions/upload", response_model=AdminSkillUploadResponse)
async def admin_upload_skill_package(
    skill_id: str,
    package: UploadFile = File(...),
    principal: AuthPrincipal = Depends(require_principal),
) -> AdminSkillUploadResponse:
    _require_skill_upload_admin(principal)
    can_upload_existing_skill = is_ai_admin(principal)
    skill_id = _safe_skill_id(skill_id)

    package_content = await _read_skill_package_upload(package)
    try:
        parsed = parse_skill_package_zip(package_content, expected_skill_id=skill_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with transaction() as conn:
        skill = await skills_catalog.get_skill(conn, skill_id=skill_id)
        is_new_skill = skill is None
        if skill is not None:
            if not can_upload_existing_skill:
                raise HTTPException(status_code=403, detail="not_ai_admin")
            if str(skill.get("status") or "") != "active":
                raise HTTPException(status_code=409, detail="skill_inactive")
        dependency_ids: list[str] = []

        if is_new_skill:
            try:
                await skills_versions.create_skill_catalog(
                    conn,
                    skill_id=skill_id,
                    name=skill_id,
                    version=parsed.content_hash,
                    description=parsed.description,
                    input_modes=["chat"],
                    output_modes=["answer"],
                    executor_type="claude-agent-worker",
                    status="active",
                )
            except platform_errors.RepositoryConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        await lock_skill_for_version_upload(conn, skill_id=skill_id)
        display_rows = await list_uploaded_skill_display_version_rows(
            conn,
            skill_ids=[skill_id],
        )
        display_versions = resolve_uploaded_skill_display_versions(display_rows)
        existing = None
        if not is_new_skill:
            existing = await skills_postgres.get_skill_version(
                conn,
                skill_id=skill_id,
                version=parsed.content_hash,
            )
        if existing is not None:
            _require_reusable_uploaded_skill_version(skill_id, existing)
            await identity_audit.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="skill_version_upload_reused",
                target_type="skill",
                target_id=skill_id,
                payload_json={
                    "skill_id": skill_id,
                    "version": parsed.content_hash,
                    "display_version": display_versions.get(
                        (skill_id, parsed.content_hash), "1.0.0"
                    ),
                    "storage_key": (existing.get("source") or {}).get("storage_key")
                    if isinstance(existing.get("source"), dict)
                    else None,
                },
            )
            return AdminSkillUploadResponse(uploaded=existing)

        display_version = next_uploaded_skill_display_version(skill_id, display_rows)
        dependency_manifests: list[dict[str, object]] = []
        storage_key = f"skills/{skill_id}/versions/{parsed.content_hash}/package.zip"
        try:
            stored = await run_storage_io(
                ObjectStorage().put_bytes,
                storage_key=storage_key,
                content=package_content,
                content_type="application/zip",
            )
        except (StorageIOBusyError, StorageIOTimeoutError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        source_json = {
            "kind": "uploaded",
            "display_version": display_version,
            "storage_key": stored.storage_key,
            "package_sha256": stored.sha256,
            "size_bytes": stored.size_bytes,
            "files": parsed.files,
        }
        if dependency_manifests:
            source_json["dependency_manifests"] = dependency_manifests
        package_contract = build_skill_package_contract(
            parsed,
            package_sha256=stored.sha256,
            storage_key=stored.storage_key,
            uploaded_by=principal.user_id,
        )
        source_json["package_contract"] = package_contract
        source_json["dependency_evidence"] = build_skill_dependency_evidence(
            dependency_ids=dependency_ids,
            dependency_manifests=dependency_manifests,
            package_contract=package_contract,
        )
        # Upload creates immutable package evidence only. Trust and tenant
        # distribution are separate, explicit review/promote transitions.
        upload_status = SKILL_VERSION_DRAFT
        uploaded = {
            "skill_id": skill_id,
            "version": parsed.content_hash,
            "content_hash": parsed.content_hash,
            "description": parsed.description,
            "source": source_json,
            "dependency_ids": dependency_ids,
            "status": upload_status,
            "created_by": principal.user_id,
            "created_at": None,
        }
        if is_new_skill:
            await identity_audit.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="skill_catalog_created_from_upload",
                target_type="skill",
                target_id=skill_id,
                payload_json={
                    "skill_id": skill_id,
                    "version": parsed.content_hash,
                    "display_version": display_version,
                    "description": parsed.description,
                    "input_modes": ["chat"],
                    "output_modes": ["answer"],
                    "executor_type": "claude-agent-worker",
                },
            )
        inserted_version = await skills_versions.upsert_skill_version(
            conn,
            skill_id=skill_id,
            version=parsed.content_hash,
            content_hash=parsed.content_hash,
            description=parsed.description,
            source_json=source_json,
            dependency_ids=dependency_ids,
            status=upload_status,
            created_by=principal.user_id,
        )
        if inserted_version is False:
            raise HTTPException(status_code=409, detail="skill_version_already_exists")
        await identity_audit.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="skill_version_uploaded",
            target_type="skill",
            target_id=skill_id,
            payload_json={
                "skill_id": skill_id,
                "version": parsed.content_hash,
                "display_version": display_version,
                "storage_key": stored.storage_key,
                "package_sha256": stored.sha256,
                "size_bytes": stored.size_bytes,
            },
        )
        response = AdminSkillUploadResponse(uploaded=uploaded)
    return response


@router.post("/admin/skills/upload/preview", response_model=PublicSkillImportPreviewResponse)
async def admin_preview_skill_package(
    file: UploadFile | None = File(default=None),
    principal: AuthPrincipal = Depends(require_principal),
) -> PublicSkillImportPreviewResponse:
    _require_skill_upload_admin(principal)
    if file is None:
        raise HTTPException(status_code=400, detail="skill_package_required")
    package_content = await _read_skill_package_upload(file)
    try:
        parsed = parse_skill_package_zip(package_content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with transaction() as conn:
        global_skill_ids = set(await skills_catalog.list_skill_ids(conn))
    return PublicSkillImportPreviewResponse(
        skill_count=1,
        skills=[
            {
                "name": parsed.skill_id,
                "description": parsed.description,
                "file_count": len(parsed.files),
                "files": [str(item.get("relative_path") or "") for item in parsed.files],
                "already_exists": parsed.skill_id in global_skill_ids,
            }
        ],
    )


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
            diff = await skills_versions.diff_skill_versions(
                conn,
                skill_id=skill_id,
                from_version=from_version,
                to_version=to_version,
            )
    except platform_errors.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AdminSkillVersionDiffResponse.model_validate(diff)


@router.post("/admin/skills/{skill_id}/versions/{version}/status", response_model=AdminSkillVersionResponse)
async def admin_update_skill_version_status(
    skill_id: str,
    version: str,
    request: AdminSkillVersionStatusRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> AdminSkillVersionResponse:
    _require_admin(principal)
    skill_id = _safe_skill_id(skill_id)
    version = _safe_version(version, "version")

    async with transaction() as conn:
        current = await skills_postgres.get_skill_version(conn, skill_id=skill_id, version=version)
        if current is None:
            raise HTTPException(status_code=404, detail="skill_version_not_found")
        current_status = normalize_skill_version_status(current.get("status"))
        if (
            request.status == "reviewed"
            and current_status not in {SKILL_VERSION_DRAFT, SKILL_VERSION_REVIEWED}
        ) or (
            current_status in {SKILL_VERSION_DISABLED, SKILL_VERSION_DEPRECATED}
            and request.status != current_status
        ):
            raise HTTPException(status_code=409, detail="skill_version_status_transition_invalid")
        review: dict[str, object] | None = None
        if request.status == "reviewed":
            review = _build_skill_version_admin_review(current)
            if review.get("status") != "passed" or review.get("blockers"):
                raise HTTPException(status_code=409, detail="skill_release_review_not_verified")
        if request.status in {SKILL_VERSION_DISABLED, SKILL_VERSION_DEPRECATED}:
            policy = await skills_versions.get_skill_release_policy(
                conn,
                tenant_id=principal.tenant_id,
                skill_id=skill_id,
            )
            if _release_policy_protects_version(policy, version):
                raise HTTPException(status_code=409, detail="skill_version_has_active_release_policy")
        try:
            updated = await skills_versions.update_skill_version_status(
                conn,
                skill_id=skill_id,
                version=version,
                status=request.status,
            )
        except platform_errors.RepositoryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await identity_audit.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="skill_version_status_changed",
            target_type="skill",
            target_id=skill_id,
            payload_json={
                "schema_version": "ai-platform.skill-version-lifecycle-audit.v1",
                "skill_id": skill_id,
                "version": version,
                "from_status": str(current.get("status") or ""),
                "to_status": request.status,
                "review_status": str((review or {}).get("status") or ""),
                "release_review": _release_review_summary(review),
            },
        )
    return AdminSkillVersionResponse.model_validate(updated)


@router.post("/admin/skills/{skill_id}/promote", response_model=AdminSkillReleasePolicyResponse)
async def admin_promote_skill_version(
    skill_id: str,
    request: AdminSkillPromoteRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> AdminSkillReleasePolicyResponse:
    _require_admin(principal)
    skill_id = _safe_skill_id(skill_id)
    async with transaction() as conn:
        version = await skills_postgres.get_skill_version(conn, skill_id=skill_id, version=request.version)
        if version is None:
            raise HTTPException(status_code=404, detail="skill_version_not_found")
        _require_releasable_skill_version(version)
        _require_materializable_skill_version(skill_id, version)
        release_review = _require_reviewed_skill_version_release(version)
        policy = await skills_versions.get_skill_release_policy(
            conn,
            tenant_id=principal.tenant_id,
            skill_id=skill_id,
            channel=request.channel,
        )
        should_deprecate_previous = request.rollout_percent == 100
        previous_version = policy["current_version"] if policy else None
        if policy is None:
            skill = await skills_catalog.get_skill(conn, skill_id=skill_id)
            if skill is None:
                raise HTTPException(status_code=404, detail="skill_not_found")
            previous_version = str(skill.get("version") or "") or None
            if request.rollout_percent < 100 and previous_version:
                previous = version if previous_version == request.version else await skills_postgres.get_skill_version(
                    conn,
                    skill_id=skill_id,
                    version=previous_version,
                )
                if previous is None:
                    raise HTTPException(status_code=409, detail="skill_version_not_materializable")
                _require_releasable_skill_version(previous)
                _require_materializable_skill_version(skill_id, previous)
        elif request.rollout_percent < 100 and previous_version:
            previous = version if previous_version == request.version else await skills_postgres.get_skill_version(
                conn,
                skill_id=skill_id,
                version=str(previous_version),
            )
            if previous is None:
                raise HTTPException(status_code=409, detail="skill_version_not_materializable")
            _require_releasable_skill_version(previous)
            _require_materializable_skill_version(skill_id, previous)
        await skills_versions.set_skill_release_policy(
            conn,
            tenant_id=principal.tenant_id,
            skill_id=skill_id,
            version=request.version,
            previous_version=previous_version,
            promoted_by=principal.user_id,
            channel=request.channel,
            rollout_percent=request.rollout_percent,
        )
        await _mark_skill_version_released(
            conn,
            skill_id=skill_id,
            version=request.version,
        )
        source = version.get("source") if isinstance(version.get("source"), dict) else {}
        if source.get("kind") == "uploaded":
            try:
                await skills_catalog.set_uploaded_workbench_skill_status(
                    conn,
                    tenant_id=principal.tenant_id,
                    skill_id=skill_id,
                    status="active",
                )
            except platform_errors.RepositoryConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        deprecated_version = None
        if should_deprecate_previous:
            deprecated_version = await _mark_superseded_skill_version_deprecated(
                conn,
                skill_id=skill_id,
                version=previous_version,
                target_version=request.version,
            )
        await identity_audit.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="skill_version_promoted",
            target_type="skill",
            target_id=skill_id,
            payload_json={
                "schema_version": "ai-platform.skill-version-release-audit.v1",
                "skill_id": skill_id,
                "from_version": previous_version,
                "to_version": request.version,
                "channel": request.channel,
                "rollout_percent": request.rollout_percent,
                "lifecycle": {
                    "released_version": request.version,
                    "deprecated_version": deprecated_version,
                    "target_status": SKILL_VERSION_RELEASED,
                },
                "release_review": _release_review_summary(release_review),
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
        version = await skills_postgres.get_skill_version(conn, skill_id=skill_id, version=request.version)
        if version is None:
            raise HTTPException(status_code=404, detail="skill_version_not_found")
        _require_rollback_target_skill_version(version)
        _require_rollback_materializable_skill_version(skill_id, version)
        release_review = _build_skill_version_admin_review(version)
        policy = await skills_versions.get_skill_release_policy(
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
        await skills_versions.set_skill_release_policy(
            conn,
            tenant_id=principal.tenant_id,
            skill_id=skill_id,
            version=request.version,
            previous_version=previous_version,
            promoted_by=principal.user_id,
            channel=request.channel,
            rollout_percent=100,
        )
        await _mark_skill_version_released(
            conn,
            skill_id=skill_id,
            version=request.version,
        )
        deprecated_version = await _mark_superseded_skill_version_deprecated(
            conn,
            skill_id=skill_id,
            version=previous_version,
            target_version=request.version,
        )
        await identity_audit.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="skill_version_rolled_back",
            target_type="skill",
            target_id=skill_id,
            payload_json={
                "schema_version": "ai-platform.skill-version-release-audit.v1",
                "skill_id": skill_id,
                "from_version": previous_version,
                "to_version": request.version,
                "channel": request.channel,
                "rollout_percent": 100,
                "lifecycle": {
                    "released_version": request.version,
                    "deprecated_version": deprecated_version,
                    "target_status": SKILL_VERSION_RELEASED,
                },
                "release_review": _release_review_summary(release_review),
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
