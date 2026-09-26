from app.skills.api import (
    configure_skill_display_version_persistence,
    configure_skill_run_admission,
)
from app.skills.application.run_admission import (
    SkillRunAdmissionPorts,
    SkillRunAdmissionService,
)
from app.skills.infrastructure import catalog_postgres, postgres, versions_postgres
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


def configure_skill_services() -> None:
    configure_skill_display_version_persistence(postgres)
    configure_skill_run_admission(
        SkillRunAdmissionService(
            SkillRunAdmissionPorts(
                catalog=catalog_postgres,
                versions=versions_postgres,
                resolve_release_decision=resolve_rollout_skill_decision,
                release_decision_payload=release_decision_payload_for_locked_version,
                is_user_runnable_status=is_user_runnable_status,
                build_manifest_pins=build_skill_version_policy_manifest_pins,
                lock_skill_version=governed_locked_skill_version,
                attach_snapshot_governance=attach_skill_snapshot_governance,
                materialization_error=SkillVersionMaterializationError,
            )
        ),
        pin_primary_skill_mcp_tool_ids,
    )
