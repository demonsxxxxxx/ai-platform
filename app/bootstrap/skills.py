from app.skills.api import (
    configure_skill_display_version_persistence,
    configure_skill_run_admission,
    resolve_worker_runtime_catalog as resolve_worker_catalog_binding,
    worker_catalog_binding,
    worker_payload_with_authorized_catalog,
    WorkerSkillDispatchAuthorization,
)
from app.skills.catalog import (
    AuthorizedSkillCatalogBinding,
    AuthorizedSkillCatalogError,
    RUNTIME_AUTHORIZED_SKILL_CATALOG_KEY,
    RUNTIME_AUTHORIZED_SKILL_MANIFESTS_KEY,
    is_current_skill_dependency_usable,
    load_runtime_authorized_skill_catalog,
)
from app.skills.application.run_admission import (
    SkillRunAdmissionPorts,
    SkillRunAdmissionService,
)
from app.skills.application.skill_markdown import configure_skill_markdown_loader
from app.skills.infrastructure.skill_markdown_yaml import load_skill_markdown_metadata
from app.skills.infrastructure import catalog_postgres, postgres, versions_postgres
from app.skills.infrastructure import resolution_postgres, run_snapshots_postgres
from app.skills import catalog as skill_catalog
from app.skills import pinning as skills_pinning
from app.platform.postgres import errors as platform_errors
from app.control_plane_contracts import LEGACY_SYNTHETIC_CHAT_SKILL_ID, RUN_EXECUTION_KIND_HARNESS_CHAT
from app.skills.infrastructure.run_snapshots_postgres import (
    pin_primary_skill_mcp_tool_ids,
)
from app.skills.lifecycle import is_user_runnable_status
from app.skills.pinning import (
    SkillVersionMaterializationError,
    attach_skill_snapshot_governance,
    build_skill_version_policy_manifest_pins,
    governed_locked_skill_version,
)
from app.skills.release_policy import (
    release_decision_payload_for_locked_version,
    resolve_rollout_skill_decision,
)
from app.skills.stager import materialize_worker_pinned_skill as stage_worker_pinned_skill


def materialize_worker_pinned_skill(skill_name, pin, snapshot_root):
    return stage_worker_pinned_skill(
        skill_name, pin, snapshot_root,
        max_file_bytes=skills_pinning.MAX_SKILL_SNAPSHOT_FILE_BYTES,
        max_total_bytes=skills_pinning.MAX_SKILL_SNAPSHOT_TOTAL_BYTES,
    )


def resolve_worker_runtime_catalog(payload):
    return resolve_worker_catalog_binding(
        payload,
        harness_execution_kind=RUN_EXECUTION_KIND_HARNESS_CHAT,
        catalog_binding_type=AuthorizedSkillCatalogBinding,
        catalog_error_type=AuthorizedSkillCatalogError,
        load_catalog=load_runtime_authorized_skill_catalog,
    )


def worker_authorized_catalog_binding(run_identity: dict[str, str]) -> AuthorizedSkillCatalogBinding:
    return worker_catalog_binding(run_identity, binding_type=AuthorizedSkillCatalogBinding)


def worker_payload_with_authorized_skill_catalog(payload, *, resolution):
    return worker_payload_with_authorized_catalog(
        payload, resolution=resolution,
        catalog_key=RUNTIME_AUTHORIZED_SKILL_CATALOG_KEY,
        manifests_key=RUNTIME_AUTHORIZED_SKILL_MANIFESTS_KEY,
    )


def build_worker_skill_dispatch_authorization() -> WorkerSkillDispatchAuthorization:
    return WorkerSkillDispatchAuthorization(
        validate_snapshots=run_snapshots_postgres.validate_run_skill_snapshots_for_dispatch,
        validate_replay=postgres.validate_replay_skill_manifests,
        resolve_skill=resolution_postgres.resolve_skill_identity,
        resolve_catalog=skill_catalog.resolve_authorized_skill_catalog,
        catalog_binding=worker_authorized_catalog_binding,
        attach_catalog=worker_payload_with_authorized_skill_catalog,
        conflict_error=platform_errors.RepositoryConflictError,
        authorization_error=platform_errors.RepositoryAuthorizationError,
        not_found_error=platform_errors.RepositoryNotFoundError,
        catalog_error=AuthorizedSkillCatalogError,
        synthetic_skill_id=LEGACY_SYNTHETIC_CHAT_SKILL_ID,
    )


def configure_skill_markdown() -> None:
    configure_skill_markdown_loader(load_skill_markdown_metadata)


def configure_skill_services() -> None:
    configure_skill_markdown()
    configure_skill_display_version_persistence(postgres)
    configure_skill_run_admission(
        SkillRunAdmissionService(
            SkillRunAdmissionPorts(
                catalog=catalog_postgres,
                versions=versions_postgres,
                resolve_release_decision=resolve_rollout_skill_decision,
                release_decision_payload=release_decision_payload_for_locked_version,
                is_user_runnable_status=is_user_runnable_status,
                dependency_is_usable=is_current_skill_dependency_usable,
                build_manifest_pins=build_skill_version_policy_manifest_pins,
                lock_skill_version=governed_locked_skill_version,
                attach_snapshot_governance=attach_skill_snapshot_governance,
                materialization_error=SkillVersionMaterializationError,
            )
        ),
        pin_primary_skill_mcp_tool_ids,
    )
