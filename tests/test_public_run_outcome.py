import pytest

from app.runs.api import (
    PUBLIC_RUN_OUTCOME_SCHEMA_VERSION,
    public_run_outcome,
)


def test_public_run_outcome_distinguishes_queued_from_started_work():
    outcome = public_run_outcome(
        run_id="run-queued",
        status="queued",
    )

    assert outcome == {
        "schema_version": PUBLIC_RUN_OUTCOME_SCHEMA_VERSION,
        "phase": "not_started",
        "detail_code": "run_queued",
        "what_happened": "任务已创建并进入队列，尚未开始执行。",
        "retained": "任务请求已保留，尚无已完成结果。",
        "next_action": "请等待任务开始；长时间没有变化时可刷新运行状态。",
        "problem_number": "run-queued",
        "artifact_count": 0,
        "completed_step_count": 0,
    }


def test_public_run_outcome_reports_pre_execution_permission_failure():
    outcome = public_run_outcome(
        run_id="run-denied",
        status="failed",
        error_code="capability_not_authorized",
    )

    assert outcome["phase"] == "failed"
    assert outcome["detail_code"] == "capability_not_authorized"
    assert outcome["retained"] == "未记录到可确认的已完成步骤或文件。"
    assert "有权使用" in str(outcome["next_action"])
    assert "重新登录" not in str(outcome["next_action"])
    assert outcome["problem_number"] == "run-denied"


def test_public_run_outcome_reports_partial_work_from_durable_facts():
    outcome = public_run_outcome(
        run_id="run-partial",
        status="failed",
        error_code="executor_deadline_exceeded",
        artifacts=[{"artifact_id": "artifact-1"}],
        steps=[
            {"status": "succeeded"},
            {"status": "failed"},
        ],
    )

    assert outcome["phase"] == "partially_completed"
    assert str(outcome["what_happened"]).startswith("任务已经开始并保留了部分成果")
    assert outcome["retained"] == "已保留 1 个已完成步骤、1 个可查看文件。"
    assert outcome["artifact_count"] == 1
    assert outcome["completed_step_count"] == 1


def test_public_run_outcome_marks_result_sync_failure_as_delivery_failure():
    outcome = public_run_outcome(
        run_id="run-delivery",
        status="failed",
        error_code="terminal_reconciliation_failed",
        artifacts=[{"artifact_id": "artifact-1"}, {"artifact_id": "artifact-2"}],
        steps=[{"status": "completed"}],
    )

    assert outcome["phase"] == "delivery_failed"
    assert "结果同步失败" in str(outcome["what_happened"])
    assert outcome["retained"] == "已保留 1 个已完成步骤、2 个可查看文件。"
    assert "下载" in str(outcome["next_action"])


def test_public_run_outcome_blocks_blind_retry_for_uncertain_tool_result():
    outcome = public_run_outcome(
        run_id="run-uncertain",
        status="failed",
        error_code="mcp_execution_outcome_unknown",
    )

    assert outcome["detail_code"] == "tool_execution_outcome_unconfirmed"
    assert "请勿重复提交" in str(outcome["next_action"])


def test_public_run_outcome_reports_answer_and_files_on_success():
    outcome = public_run_outcome(
        run_id="run-success",
        status="succeeded",
        artifacts=[{"artifact_id": "artifact-1"}],
        answer_available=True,
    )

    assert outcome["phase"] == "completed"
    assert outcome["retained"] == "聊天正文和 1 个可查看文件均已保留。"


def test_public_run_outcome_does_not_mislabel_runtime_capability_failure_as_login_problem():
    outcome = public_run_outcome(
        run_id="run-runtime-capability",
        status="failed",
        error_code="required_tool_unavailable",
    )

    assert outcome["detail_code"] == "required_capability_unavailable"
    assert "专家或工具配置" in str(outcome["next_action"])
    assert "重新登录" not in str(outcome["next_action"])


@pytest.mark.parametrize(
    ("error_code", "expected_action", "forbidden_action"),
    [
        ("required_capability_unavailable", "专家或工具配置", "重新登录"),
        ("capability_not_authorized", "有权使用", "重新登录"),
        ("context_file_storage_unavailable", "稍后重试", "重新上传"),
        ("context_file_staging_write_failed", "稍后重试", "重新上传"),
        ("current_request_too_large", "缩短或拆分当前请求", "重新上传"),
        ("claude_agent_sdk_input_context_too_large", "减少附件", "服务不可用"),
        ("claude_agent_sdk_input_image_invalid", "图片格式", "服务不可用"),
        ("model_capacity_missing", "模型运行配置", "可以重试"),
        ("mcp_execution_outcome_unknown", "请勿重复提交", "可以重试"),
        ("terminal_reconciliation_failed", "刷新会话", "可以重试"),
    ],
)
def test_public_run_outcome_recovery_matches_failure_cause(error_code, expected_action, forbidden_action):
    outcome = public_run_outcome(run_id="run-safe", status="failed", error_code=error_code)
    assert expected_action in outcome["next_action"]
    assert forbidden_action not in outcome["next_action"]
