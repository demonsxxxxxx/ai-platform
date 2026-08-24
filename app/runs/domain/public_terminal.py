"""Public terminal taxonomy owned by the Runs bounded context."""


def normalize_run_status(status: str) -> str:
    return "cancelled" if status == "canceled" else status


PUBLIC_TERMINAL_DETAIL_MESSAGES = {
    "run_failed": "任务未能完成。请稍后重试；如问题持续，请联系管理员。",
    "run_timeout": "任务执行超时。请缩小任务范围后重试。",
    "run_budget_exhausted": "任务已达到执行轮次上限。请缩小或拆分任务后重试。",
    "model_service_unavailable": "模型服务暂时不可用。请稍后重试；如问题持续，请联系管理员。",
    "execution_service_unavailable": "AI 执行服务暂时不可用。请稍后重试；如问题持续，请联系管理员。",
    "dependent_service_unavailable": "任务依赖的服务暂时不可用。请稍后重试。",
    "capability_not_authorized": "当前账号不能使用所选能力。请重新选择或联系管理员。",
    "tool_permission_denied": "任务所需工具未获授权。请调整请求或联系管理员。",
    "tool_invocation_evidence_mismatch": "工具调用证据未完整确认（tool_invocation_evidence_mismatch）。请重试；如问题持续，请联系管理员。",
    "required_capability_unavailable": "任务所需执行能力当前不可用。请调整请求或联系管理员。",
    "skill_sandbox_admission_failed": "所选 Skill 未能通过隔离沙箱准入。请调整 Skill 或联系管理员。",
    "context_file_too_large": "文件超过 32 MB 处理上限。请选择更小的文件后重试。",
    "context_file_pdf_password_required": "PDF 文件需要密码。请先解除密码保护后重新上传。",
    "context_file_password_required": "文件受密码保护。请先解除密码保护后重新上传。",
    "context_file_unsafe_content": "文件包含不允许的活动内容、宏或外部引用。请导出安全副本后重试。",
    "context_file_page_limit_exceeded": "PDF 页数超过处理上限。请拆分文件后重试。",
    "context_file_processing_limit_exceeded": "文件结构或内容数量超过处理上限。请拆分或精简文件后重试。",
    "context_file_invalid": "文件已损坏或无法解析。请重新导出文件后上传。",
    "context_file_encoding_unsupported": "文件文本编码暂不支持。请转换为 UTF-8 后重新上传。",
    "context_file_type_unsupported": "文件实际格式与声明类型不一致，或该格式暂不支持。",
    "context_file_identity_mismatch": "文件完整性校验失败。请重新上传该文件。",
    "context_file_unavailable": "文件当前不可用或已失去访问权限。请重新上传后重试。",
    "context_file_name_conflict": "多个附件名称冲突。请重命名后重新上传。",
    "context_file_storage_unavailable": "文件存储服务暂时不可用。请稍后重试。",
    "context_file_staging_unavailable": "文件暂存失败。请稍后重试；如问题持续，请联系管理员。",
    "context_file_parser_contract_invalid": "文件处理器未能验证输入。请重新上传；如问题持续，请联系管理员。",
    "context_file_preprocessing_failed": "文件预处理失败。请重新导出后上传；如问题持续，请联系管理员。",
    "run_cancelled": "任务已取消。取消前已产生的公开内容仍会保留。",
}

PUBLIC_TERMINAL_ERROR_CODE_ALIASES = {
    "native_tool_admission_failed": "skill_sandbox_admission_failed",
    "attachment_materialized_fact_invalid": "context_file_identity_mismatch",
    "attachment_parser_file_mapping_invalid": "context_file_identity_mismatch",
    "attachment_parser_file_too_large": "context_file_too_large",
    "attachment_parser_prompt_too_large": "context_file_processing_limit_exceeded",
    "attachment_parser_staged_file_invalid": "context_file_invalid",
    "attachment_parser_staged_file_mismatch": "context_file_identity_mismatch",
    "attachment_parser_unsupported": "context_file_type_unsupported",
    "attachment_preprocessing_contract_invalid": "context_file_parser_contract_invalid",
    "context_file_too_large": "context_file_too_large",
    "context_file_pdf_password_required": "context_file_pdf_password_required",
    "context_file_pdf_active_content_unsupported": "context_file_unsafe_content",
    "context_file_docx_embedded_content_unsupported": "context_file_unsafe_content",
    "context_file_docx_external_relationship_unsupported": "context_file_unsafe_content",
    "context_file_docx_macros_unsupported": "context_file_unsafe_content",
    "xlsx_macros_unsupported": "context_file_unsafe_content",
    "context_file_pdf_page_limit_exceeded": "context_file_page_limit_exceeded",
    "context_file_pdf_parse_failed": "context_file_invalid",
    "context_file_docx_archive_invalid": "context_file_invalid",
    "context_file_docx_archive_entry_limit_exceeded": "context_file_processing_limit_exceeded",
    "context_file_docx_archive_structure_invalid": "context_file_invalid",
    "context_file_docx_archive_too_large": "context_file_processing_limit_exceeded",
    "context_file_docx_encrypted": "context_file_password_required",
    "context_file_docx_parse_failed": "context_file_invalid",
    "context_file_docx_relationship_invalid": "context_file_invalid",
    "context_file_docx_required_part_missing": "context_file_invalid",
    "context_file_json_invalid": "context_file_invalid",
    "context_file_staging_write_failed": "context_file_staging_unavailable",
    "context_file_text_encoding_unsupported": "context_file_encoding_unsupported",
    "xlsx_archive_too_large": "context_file_processing_limit_exceeded",
    "xlsx_cell_limit_exceeded": "context_file_processing_limit_exceeded",
    "xlsx_content_types_structure_unsupported": "context_file_invalid",
    "xlsx_encrypted_unsupported": "context_file_password_required",
    "xlsx_relationship_structure_unsupported": "context_file_invalid",
    "xlsx_workbook_part_unsupported": "context_file_invalid",
    "xlsx_workbook_structure_unsupported": "context_file_invalid",
    "xlsx_worksheet_structure_unsupported": "context_file_invalid",
    "xlsx_xml_encoding_unsupported": "context_file_encoding_unsupported",
    "xlsx_xml_entities_unsupported": "context_file_unsafe_content",
    "xlsx_parse_failed": "context_file_invalid",
    "context_file_type_unsupported": "context_file_type_unsupported",
    "context_file_identity_mismatch": "context_file_identity_mismatch",
    "context_file_unavailable": "context_file_unavailable",
    "context_file_name_conflict": "context_file_name_conflict",
    "context_file_storage_unavailable": "context_file_storage_unavailable",
    "context_file_preprocessing_failed": "context_file_preprocessing_failed",
    "executor_deadline_exceeded": "run_timeout",
    "executor_cleanup_timeout": "run_timeout",
    "claude_agent_sdk_turn_limit_exceeded": "run_budget_exhausted",
    "claude_agent_sdk_runtime_error": "model_service_unavailable",
    "claude_agent_sdk_disabled": "execution_service_unavailable",
    "claude_agent_sdk_import_failed": "execution_service_unavailable",
    "claude_agent_sdk_unavailable": "execution_service_unavailable",
    "docker_unavailable": "execution_service_unavailable",
    "executor_health_timeout": "execution_service_unavailable",
    "executor_runner_failed": "execution_service_unavailable",
    "ragflow_api_error": "dependent_service_unavailable",
    "capability_not_authorized": "capability_not_authorized",
    "model_not_allowed": "capability_not_authorized",
    "tool_denied": "tool_permission_denied",
    "mcp_tool_denied": "tool_permission_denied",
    "tool_permission_denied": "tool_permission_denied",
    "tool_invocation_evidence_mismatch": "tool_invocation_evidence_mismatch",
    "required_tool_unavailable": "required_capability_unavailable",
    "required_tool_declaration_mismatch": "required_capability_unavailable",
    "required_tool_scope_mismatch": "required_capability_unavailable",
    "required_tool_not_currently_authorized": "required_capability_unavailable",
    "required_tool_admin_bypass_forbidden": "required_capability_unavailable",
    "required_tool_completion_evidence_missing": "required_capability_unavailable",
    "required_tool_completion_evidence_mismatch": "required_capability_unavailable",
}

CHAT_PUBLIC_PROJECTION_VERSION = "ai-platform.chat-public-projection.v1"


def public_terminal_projection(
    status: object,
    error_code: object = None,
) -> dict[str, object] | None:
    """Build the sole ordinary-user projection for failed or cancelled terminals."""
    normalized_status = normalize_run_status(str(status or ""))
    if normalized_status == "cancelled":
        detail_code = "run_cancelled"
        detail_kind = "cancelled"
    elif normalized_status == "failed":
        raw_error_code = str(error_code or "").strip()
        detail_code = PUBLIC_TERMINAL_ERROR_CODE_ALIASES.get(raw_error_code, "run_failed")
        detail_kind = "failed"
    else:
        return None
    message = PUBLIC_TERMINAL_DETAIL_MESSAGES[detail_code]
    return {
        "detail_kind": detail_kind,
        "detail_code": detail_code,
        "message": message,
        "error_code": detail_code if detail_kind == "failed" else None,
        "result": {"message": message},
        "event_payload": {},
    }


def public_terminal_detail(status: object, error_code: object = None) -> dict[str, str] | None:
    """Return the stable public terminal taxonomy used by compatibility clients."""
    projection = public_terminal_projection(status, error_code)
    if projection is None:
        return None
    return {
        "detail_kind": str(projection["detail_kind"]),
        "detail_code": str(projection["detail_code"]),
        "message": str(projection["message"]),
    }
