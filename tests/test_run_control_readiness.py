from app.auth import AuthPrincipal
from app.run_control_readiness import run_control_readiness_snapshot


def _principal(*, roles: list[str] | None = None) -> AuthPrincipal:
    return AuthPrincipal(
        tenant_id="default",
        user_id="user-a",
        display_name="User A",
        roles=roles or ["user"],
    )


def test_readiness_module_projects_public_resume_without_private_step_payload():
    run = {
        "id": "run-ready",
        "session_id": "ses-ready",
        "workspace_id": "default",
        "agent_id": "qa-word-review",
        "skill_id": "qa-file-reviewer",
        "schema_version": "ai-platform.run.v1",
        "executor_schema_version": "ai-platform.executor-result.v1",
        "status": "failed",
        "trace_id": "trace-ready",
        "input_json": {"message": "review", "skill_id": "qa-file-reviewer"},
        "result_json": {},
        "error_code": None,
        "error_message": "qa-file-reviewer failed in qa-word-review",
        "cancel_requested_at": None,
        "cancel_requested_by": None,
    }
    steps = [
        {
            "id": "step-skill",
            "run_id": "run-ready",
            "step_key": "qa-file-reviewer",
            "step_kind": "agent",
            "status": "succeeded",
            "title": "qa-file-reviewer",
            "role": "qa-word-review",
            "sequence": 1,
            "payload_json": {
                "output": "raw reusable output must not leak",
                "resource_limits": {"max_seconds": 60},
                "sandbox_mode": "ephemeral",
                "private_payload": {"token": "secret-token"},
            },
            "started_at": None,
            "finished_at": None,
            "created_at": None,
            "updated_at": None,
        }
    ]

    snapshot = run_control_readiness_snapshot(
        run=run,
        steps=steps,
        principal=_principal(),
        queue_insight=None,
    )

    assert snapshot["contract_version"] == "ai-platform.run-control-readiness.v1"
    fixed_failure_message = "任务未能完成。请稍后重试；如问题持续，请联系管理员。"
    assert snapshot["run"]["error_message"] == fixed_failure_message
    assert "qa-file-reviewer" not in fixed_failure_message
    assert "qa-word-review" not in fixed_failure_message
    assert snapshot["actions"]["resume"]["enabled"] is True
    assert snapshot["actions"]["resume"]["reason"] == "checkpoint_outputs_available"
    assert snapshot["checkpoint_candidates"] == [
        {
            "step_id": "step-skill",
            "step_key": "step-skill",
            "status": "succeeded",
            "title": "步骤已完成",
            "role": None,
            "sequence": 1,
            "reusable": True,
            "reason": "output_available",
        }
    ]
    public_dump = str(snapshot)
    assert "qa-file-reviewer" not in public_dump
    assert "qa-word-review" not in public_dump
    assert "raw reusable output" not in public_dump
    assert "resource_limits" not in public_dump
    assert "sandbox_mode" not in public_dump
    assert "secret-token" not in public_dump
