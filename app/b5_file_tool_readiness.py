from __future__ import annotations

from typing import Any

from app.tool_policy_readiness import build_tool_policy_readiness


SCHEMA_VERSION = "ai-platform.b5-file-tool-readiness.v1"
BACKEND_STAGE = "B5 files/artifacts/tool permission governance"


def _file_artifact_authority_domain() -> dict[str, Any]:
    return {
        "gate_slice": "B5a file/artifact authority",
        "status": "local_contract_recorded",
        "implemented_controls": [
            "file_lookup_scoped_by_tenant",
            "run_lookup_scoped_by_tenant_run_and_user",
            "artifact_owner_tenant_acl_download",
            "artifact_admin_same_tenant_audit_fallback",
            "artifact_preview_owner_acl_and_content_type_allowlist",
            "unauthorized_artifact_preview_denies_before_storage_read",
            "artifact_public_projection_hides_storage_key",
        ],
        "source_tests": [
            "tests/test_artifact_permissions.py",
            "tests/test_two_user_artifact_isolation.py",
            "tests/test_admin_run_detail.py",
        ],
        "open_gaps": [
            "file_upload_namespace_retention_runtime_smoke",
            "211_file_to_artifact_unauthorized_denial_smoke",
            "artifact_expiry_and_deleted_state_runtime_smoke",
        ],
        "evidence_policy": (
            "Local route/repository tests prove scoped artifact lookup, preview "
            "allowlist, and denial-before-storage-read behavior. Runtime B5a "
            "closure still requires a selected workflow smoke with upload, run, "
            "artifact preview/download, unauthorized denial, retention, and cleanup."
        ),
    }


def _exact_tool_permission_domain() -> dict[str, Any]:
    tool_policy = build_tool_policy_readiness()
    return {
        "gate_slice": "B5b exact tool permission",
        "status": "local_contract_recorded",
        "implemented_controls": [
            "tool_permission_request_scoped_to_tenant_user_run",
            "tool_permission_public_projection_hides_raw_payloads",
            "exact_tool_permission_decision_lookup_source_tests",
            "allow_once_replay_denial_source_tests",
            "disabled_or_unregistered_tool_denial_source_tests",
            "risk_write_fail_closed_policy_evaluation",
        ],
        "source_tests": [
            "tests/test_tool_permission_routes.py",
            "tests/test_admin_tool_policies.py",
            "tests/test_worker.py::test_worker_consumes_allow_once_mcp_decision_before_dispatch",
            "tests/test_worker.py::test_worker_fails_closed_when_allow_once_mcp_decision_cannot_be_consumed",
        ],
        "policy_evidence": {
            "schema_version": tool_policy["schema_version"],
            "status": tool_policy["status"],
            "registry_contract": tool_policy["registry_contract"],
            "summary": tool_policy["summary"],
            "open_gaps": tool_policy["open_gaps"],
        },
        "open_gaps": [
            "shell_network_filesystem_mcp_runtime_replay_denial_smoke",
            "ordinary_user_tool_permission_card_visual_acceptance",
            "legacy_frontend_route_policy_enforcement_or_ai_platform_remap",
        ],
        "evidence_policy": (
            "Local source tests prove exact decision lookup and allow_once "
            "consume semantics. Runtime B5b closure still requires shell, "
            "network, filesystem, and MCP replay-denial evidence for a named run."
        ),
    }


def build_b5_file_tool_readiness() -> dict[str, Any]:
    """Build a B5 local readiness snapshot without claiming runtime closure."""
    domains = {
        "file_artifact_authority": _file_artifact_authority_domain(),
        "exact_tool_permission": _exact_tool_permission_domain(),
    }
    open_gaps = [
        "file_upload_namespace_retention_runtime_smoke",
        "artifact_preview_download_unauthorized_denial_211_smoke",
        "exact_tool_permission_runtime_replay_denial_smoke",
        "projection_redaction_runtime_acceptance",
        "b5_issue_review_and_closure_evidence",
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "backend_stage": BACKEND_STAGE,
        "status": "partial_blocked",
        "status_label": "local partial",
        "issue": "#150",
        "domains": domains,
        "open_gaps": open_gaps,
        "claim_boundary": {
            "does_not_create_211_verified": True,
            "does_not_close_b5_g6_g7_g9": True,
            "does_not_enable_product_beta": True,
            "does_not_enable_ordinary_user_high_risk_tools": True,
            "does_not_claim_file_artifact_runtime_workflow_proof": True,
        },
        "non_expansion_invariants": {
            "production_claim_allowed": False,
            "department_rollout_allowed": False,
            "ordinary_user_high_risk_tools_allowed": False,
            "runtime_tool_replay_denial_claimed": False,
            "file_artifact_runtime_workflow_claimed": False,
        },
        "evidence_policy": (
            "B5 local readiness records source contracts and focused tests only. "
            "B5 cannot be 211 verified or gate closable until a reviewed runtime "
            "workflow proves upload, governed run, artifact access, unauthorized "
            "denial, exact tool decision replay denial, projection redaction, "
            "issue review, merge, and residual caveats."
        ),
    }


def render_b5_file_tool_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render B5 readiness as operator-readable Markdown."""
    gaps = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    file_domain = readiness["domains"]["file_artifact_authority"]
    tool_domain = readiness["domains"]["exact_tool_permission"]
    file_controls = "\n".join(f"- {item}" for item in file_domain["implemented_controls"])
    file_gaps = "\n".join(f"- {item}" for item in file_domain["open_gaps"])
    tool_controls = "\n".join(f"- {item}" for item in tool_domain["implemented_controls"])
    tool_gaps = "\n".join(f"- {item}" for item in tool_domain["open_gaps"])
    boundary = readiness["claim_boundary"]
    boundary_lines = [
        "- does not create `211 verified`",
        "- does not close B5/G6/G7/G9",
        "- does not enable product beta",
    ]
    if not boundary.get("does_not_create_211_verified"):
        raise RuntimeError("b5_claim_boundary_regression")
    return (
        "# ai-platform B5 File/Tool Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Stage: `{readiness['backend_stage']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Status label: `{readiness['status_label']}`\n\n"
        "## Open Gaps\n\n"
        f"{gaps}\n\n"
        "## B5a File And Artifact Authority\n\n"
        f"Status: `{file_domain['status']}`\n\n"
        "Implemented controls:\n\n"
        f"{file_controls}\n\n"
        "Open gaps:\n\n"
        f"{file_gaps}\n\n"
        "## B5b Exact Tool Permission\n\n"
        f"Status: `{tool_domain['status']}`\n\n"
        "Implemented controls:\n\n"
        f"{tool_controls}\n\n"
        "Open gaps:\n\n"
        f"{tool_gaps}\n\n"
        "## Claim Boundary\n\n"
        + "\n".join(boundary_lines)
        + "\n\n"
        "## Evidence Policy\n\n"
        f"{readiness['evidence_policy']}\n"
    )
