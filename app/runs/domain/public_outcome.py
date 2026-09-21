"""Stable, ordinary-user outcome summaries for Run status surfaces."""

from collections.abc import Iterable, Mapping

from app.runs.domain.public_terminal import (
    normalize_run_status,
    public_terminal_projection,
)


PUBLIC_RUN_OUTCOME_SCHEMA_VERSION = "ai-platform.public-run-outcome.v1"

_COMPLETED_STEP_STATUSES = frozenset({"completed", "reused", "succeeded", "success"})
_PERMISSION_DETAIL_CODES = frozenset(
    {
        "capability_not_authorized",
        "tool_permission_denied",
    }
)
_CAPABILITY_CONFIGURATION_DETAIL_CODES = frozenset(
    {
        "required_capability_unavailable",
        "skill_sandbox_admission_failed",
    }
)
_FILE_DETAIL_CODES = frozenset(
    {
        "context_file_too_large",
        "context_file_pdf_password_required",
        "context_file_password_required",
        "context_file_unsafe_content",
        "context_file_page_limit_exceeded",
        "context_file_processing_limit_exceeded",
        "context_file_invalid",
        "context_file_encoding_unsupported",
        "context_file_type_unsupported",
        "context_file_identity_mismatch",
        "context_file_unavailable",
        "context_file_name_conflict",
        "context_file_storage_unavailable",
        "context_file_staging_unavailable",
        "context_file_parser_contract_invalid",
        "context_file_preprocessing_failed",
        "current_request_too_large",
    }
)


def _completed_step_count(steps: Iterable[Mapping[str, object]]) -> int:
    return sum(
        1
        for step in steps
        if str(step.get("status") or "").strip().lower() in _COMPLETED_STEP_STATUSES
    )


def _retained_summary(*, artifact_count: int, completed_step_count: int) -> str:
    retained: list[str] = []
    if completed_step_count:
        retained.append(f"{completed_step_count} 个已完成步骤")
    if artifact_count:
        retained.append(f"{artifact_count} 个可查看文件")
    if retained:
        return f"已保留 {'、'.join(retained)}。"
    return "未记录到可确认的已完成步骤或文件。"


def _next_action(*, detail_code: str, artifact_count: int) -> str:
    if detail_code == "tool_execution_outcome_unconfirmed":
        return "请勿重复提交，以免重复执行外部操作；请联系管理员并提供问题编号。"
    if detail_code == "terminal_reconciliation_failed":
        if artifact_count:
            return "可先查看或下载已保留文件，再刷新会话；如正文仍缺失，请联系管理员并提供问题编号。"
        return "请刷新会话；如结果仍未出现，请联系管理员并提供问题编号。"
    if detail_code in _PERMISSION_DETAIL_CODES:
        return "请重新登录后再试；仍无权限时，请联系管理员并提供问题编号。"
    if detail_code in _CAPABILITY_CONFIGURATION_DETAIL_CODES:
        return "请检查所选专家或工具配置；如仍不可用，请联系管理员并提供问题编号。"
    if detail_code in _FILE_DETAIL_CODES:
        return "请按提示调整或重新上传输入文件后再试。"
    if artifact_count:
        return "可先查看或下载已保留文件，再重试；如问题持续，请联系管理员并提供问题编号。"
    return "可以重试；如问题持续，请联系管理员并提供问题编号。"


def public_run_outcome(
    *,
    run_id: object,
    status: object,
    error_code: object = None,
    artifacts: Iterable[Mapping[str, object]] = (),
    steps: Iterable[Mapping[str, object]] = (),
    answer_available: bool = False,
) -> dict[str, object]:
    """Answer the four user-facing outcome questions from durable Run facts only."""

    normalized_status = normalize_run_status(str(status or ""))
    problem_number = str(run_id or "")
    artifact_count = sum(1 for _ in artifacts)
    completed_step_count = _completed_step_count(steps)
    retained = _retained_summary(
        artifact_count=artifact_count,
        completed_step_count=completed_step_count,
    )

    if normalized_status == "queued":
        phase = "not_started"
        what_happened = "任务已创建并进入队列，尚未开始执行。"
        retained = "任务请求已保留，尚无已完成结果。"
        next_action = "请等待任务开始；长时间没有变化时可刷新运行状态。"
        detail_code = "run_queued"
    elif normalized_status == "running":
        phase = "in_progress"
        what_happened = "任务已经开始，当前仍在后台执行。"
        next_action = "可继续等待或刷新运行状态，请勿重复提交同一任务。"
        detail_code = "run_running"
    elif normalized_status == "succeeded":
        phase = "completed"
        what_happened = "任务已完成。"
        if answer_available and artifact_count:
            retained = f"聊天正文和 {artifact_count} 个可查看文件均已保留。"
        elif answer_available:
            retained = "聊天正文已保留。"
        next_action = "可以查看聊天正文和已生成文件。"
        detail_code = "run_succeeded"
    else:
        terminal = public_terminal_projection(normalized_status, error_code)
        detail_code = str(terminal["detail_code"]) if terminal is not None else "run_failed"
        what_happened = (
            str(terminal["message"])
            if terminal is not None
            else "任务状态暂时无法确认。"
        )
        if normalized_status == "cancelled":
            phase = "cancelled"
            next_action = "如仍需完成任务，可以重新提交；取消前保留的文件仍可查看。"
        elif detail_code == "terminal_reconciliation_failed":
            phase = "delivery_failed"
            what_happened = "任务主体执行已经结束，但最终结果同步失败。"
            next_action = _next_action(
                detail_code=detail_code,
                artifact_count=artifact_count,
            )
        elif completed_step_count > 0 or artifact_count > 0:
            phase = "partially_completed"
            what_happened = f"任务已经开始并保留了部分成果；{what_happened}"
            next_action = _next_action(
                detail_code=detail_code,
                artifact_count=artifact_count,
            )
        else:
            phase = "failed"
            next_action = _next_action(
                detail_code=detail_code,
                artifact_count=artifact_count,
            )

    return {
        "schema_version": PUBLIC_RUN_OUTCOME_SCHEMA_VERSION,
        "phase": phase,
        "detail_code": detail_code,
        "what_happened": what_happened,
        "retained": retained,
        "next_action": next_action,
        "problem_number": problem_number,
        "artifact_count": artifact_count,
        "completed_step_count": completed_step_count,
    }
