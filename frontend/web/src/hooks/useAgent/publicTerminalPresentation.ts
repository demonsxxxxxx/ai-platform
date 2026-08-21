import i18n from "../../i18n";

export interface PublicTerminalPresentationDefinition {
  detailKind: "failed" | "cancelled" | "result_unavailable";
  messageKey: string;
  defaultMessage: string;
  eventLabelKey: string;
  defaultEventLabel: string;
  stage: string;
  severity: "warning" | "error";
}

function failed(
  messageKey: string,
  defaultMessage: string,
  eventLabelKey: string,
  defaultEventLabel: string,
  stage = "terminal",
): PublicTerminalPresentationDefinition {
  return {
    detailKind: "failed",
    messageKey,
    defaultMessage,
    eventLabelKey,
    defaultEventLabel,
    stage,
    severity: "error",
  };
}

export const PUBLIC_TERMINAL_PRESENTATION_DEFINITIONS = {
  run_failed: failed(
    "chat.runTerminal.failed",
    "任务未能完成。请稍后重试；如问题持续，请联系管理员。",
    "chat.runStatus.event.runFailed",
    "执行失败",
  ),
  run_timeout: failed(
    "chat.runTerminal.runTimeout",
    "任务执行超时。请缩小任务范围后重试。",
    "chat.runStatus.event.runTimeout",
    "执行已超时",
  ),
  run_budget_exhausted: failed(
    "chat.runTerminal.runBudgetExhausted",
    "任务已达到执行轮次上限。请缩小或拆分任务后重试。",
    "chat.runStatus.event.runBudgetExhausted",
    "已达到执行上限",
  ),
  model_service_unavailable: failed(
    "chat.runTerminal.modelServiceUnavailable",
    "模型服务暂时不可用。请稍后重试；如问题持续，请联系管理员。",
    "chat.runStatus.event.modelServiceUnavailable",
    "模型服务不可用",
  ),
  execution_service_unavailable: failed(
    "chat.runTerminal.executionServiceUnavailable",
    "AI 执行服务暂时不可用。请稍后重试；如问题持续，请联系管理员。",
    "chat.runStatus.event.executionServiceUnavailable",
    "执行服务不可用",
  ),
  dependent_service_unavailable: failed(
    "chat.runTerminal.dependentServiceUnavailable",
    "任务依赖的服务暂时不可用。请稍后重试。",
    "chat.runStatus.event.dependentServiceUnavailable",
    "依赖服务不可用",
  ),
  capability_not_authorized: failed(
    "chat.runTerminal.capabilityNotAuthorized",
    "当前账号不能使用所选能力。请重新选择或联系管理员。",
    "chat.runStatus.event.capabilityNotAuthorized",
    "能力未获授权",
    "policy",
  ),
  tool_permission_denied: failed(
    "chat.runTerminal.toolPermissionDenied",
    "任务所需工具未获授权。请调整请求或联系管理员。",
    "chat.runStatus.event.toolPermissionDenied",
    "操作未获授权",
    "policy",
  ),
  tool_invocation_evidence_mismatch: failed(
    "chat.runTerminal.toolInvocationEvidenceMismatch",
    "工具调用证据未完整确认（tool_invocation_evidence_mismatch）。请重试；如问题持续，请联系管理员。",
    "chat.runStatus.event.toolInvocationEvidenceMismatch",
    "工具调用证据未完整确认",
    "tool_evidence",
  ),
  required_capability_unavailable: failed(
    "chat.runTerminal.requiredCapabilityUnavailable",
    "任务所需执行能力当前不可用。请调整请求或联系管理员。",
    "chat.runStatus.event.requiredCapabilityUnavailable",
    "所需执行能力不可用",
    "policy",
  ),
  skill_sandbox_admission_failed: failed(
    "chat.runTerminal.skillSandboxAdmissionFailed",
    "所选 Skill 未能通过隔离沙箱准入。请调整 Skill 或联系管理员。",
    "chat.runStatus.event.skillSandboxAdmissionFailed",
    "Skill 沙箱不可用",
    "skill_sandbox_admission",
  ),
  context_file_too_large: failed(
    "chat.runTerminal.contextFileTooLarge",
    "文件超过 32 MB 处理上限。请选择更小的文件后重试。",
    "chat.runStatus.event.contextFileTooLarge",
    "文件超过处理上限",
    "file_preprocessing",
  ),
  context_file_pdf_password_required: failed(
    "chat.runTerminal.contextFilePdfPasswordRequired",
    "PDF 文件需要密码。请先解除密码保护后重新上传。",
    "chat.runStatus.event.contextFilePdfPasswordRequired",
    "PDF 文件需要密码",
    "file_preprocessing",
  ),
  context_file_password_required: failed(
    "chat.runTerminal.contextFilePasswordRequired",
    "文件受密码保护。请先解除密码保护后重新上传。",
    "chat.runStatus.event.contextFilePasswordRequired",
    "文件受密码保护",
    "file_preprocessing",
  ),
  context_file_unsafe_content: failed(
    "chat.runTerminal.contextFileUnsafeContent",
    "文件包含不允许的活动内容、宏或外部引用。请导出安全副本后重试。",
    "chat.runStatus.event.contextFileUnsafeContent",
    "文件包含不允许的内容",
    "file_preprocessing",
  ),
  context_file_page_limit_exceeded: failed(
    "chat.runTerminal.contextFilePageLimitExceeded",
    "PDF 页数超过处理上限。请拆分文件后重试。",
    "chat.runStatus.event.contextFilePageLimitExceeded",
    "PDF 页数超过处理上限",
    "file_preprocessing",
  ),
  context_file_processing_limit_exceeded: failed(
    "chat.runTerminal.contextFileProcessingLimitExceeded",
    "文件结构或内容数量超过处理上限。请拆分或精简文件后重试。",
    "chat.runStatus.event.contextFileProcessingLimitExceeded",
    "文件内容超过处理上限",
    "file_preprocessing",
  ),
  context_file_invalid: failed(
    "chat.runTerminal.contextFileInvalid",
    "文件已损坏或无法解析。请重新导出文件后上传。",
    "chat.runStatus.event.contextFileInvalid",
    "文件已损坏或无法解析",
    "file_preprocessing",
  ),
  context_file_encoding_unsupported: failed(
    "chat.runTerminal.contextFileEncodingUnsupported",
    "文件文本编码暂不支持。请转换为 UTF-8 后重新上传。",
    "chat.runStatus.event.contextFileEncodingUnsupported",
    "文件编码不受支持",
    "file_preprocessing",
  ),
  context_file_type_unsupported: failed(
    "chat.runTerminal.contextFileTypeUnsupported",
    "文件实际格式与声明类型不一致，或该格式暂不支持。",
    "chat.runStatus.event.contextFileTypeUnsupported",
    "文件类型不受支持",
    "file_preprocessing",
  ),
  context_file_identity_mismatch: failed(
    "chat.runTerminal.contextFileIdentityMismatch",
    "文件完整性校验失败。请重新上传该文件。",
    "chat.runStatus.event.contextFileIdentityMismatch",
    "文件完整性校验失败",
    "file_preprocessing",
  ),
  context_file_unavailable: failed(
    "chat.runTerminal.contextFileUnavailable",
    "文件当前不可用或已失去访问权限。请重新上传后重试。",
    "chat.runStatus.event.contextFileUnavailable",
    "文件不可用",
    "file_preprocessing",
  ),
  context_file_name_conflict: failed(
    "chat.runTerminal.contextFileNameConflict",
    "多个附件名称冲突。请重命名后重新上传。",
    "chat.runStatus.event.contextFileNameConflict",
    "附件名称冲突",
    "file_preprocessing",
  ),
  context_file_storage_unavailable: failed(
    "chat.runTerminal.contextFileStorageUnavailable",
    "文件存储服务暂时不可用。请稍后重试。",
    "chat.runStatus.event.contextFileStorageUnavailable",
    "文件存储服务不可用",
    "file_preprocessing",
  ),
  context_file_staging_unavailable: failed(
    "chat.runTerminal.contextFileStagingUnavailable",
    "文件暂存失败。请稍后重试；如问题持续，请联系管理员。",
    "chat.runStatus.event.contextFileStagingUnavailable",
    "文件暂存失败",
    "file_preprocessing",
  ),
  context_file_parser_contract_invalid: failed(
    "chat.runTerminal.contextFileParserContractInvalid",
    "文件处理器未能验证输入。请重新上传；如问题持续，请联系管理员。",
    "chat.runStatus.event.contextFileParserContractInvalid",
    "文件处理器验证失败",
    "file_preprocessing",
  ),
  context_file_preprocessing_failed: failed(
    "chat.runTerminal.contextFilePreprocessingFailed",
    "文件预处理失败。请重新导出后上传；如问题持续，请联系管理员。",
    "chat.runStatus.event.contextFilePreprocessingFailed",
    "文件预处理失败",
    "file_preprocessing",
  ),
  run_cancelled: {
    detailKind: "cancelled",
    messageKey: "chat.runTerminal.cancelledWithPartial",
    defaultMessage: "任务已取消。取消前已产生的公开内容仍会保留。",
    eventLabelKey: "chat.runStatus.event.runCancelled",
    defaultEventLabel: "执行已取消",
    stage: "terminal",
    severity: "warning",
  },
  terminal_reconciliation_failed: failed(
    "chat.runTerminal.terminalReconciliationFailed",
    "任务执行已结束，但结果同步失败（terminal_reconciliation_failed）。已保留可恢复的内容；请刷新会话或联系管理员并提供任务编号。",
    "chat.runStatus.event.terminalReconciliationFailed",
    "任务结果同步失败",
    "terminal_reconciliation",
  ),
  result_unavailable: {
    detailKind: "result_unavailable",
    messageKey: "chat.runTerminal.resultUnavailable",
    defaultMessage: "本次执行未能生成可展示的回复内容。",
    eventLabelKey: "chat.runStatus.event.resultUnavailable",
    defaultEventLabel: "未生成可展示的回复",
    stage: "terminal",
    severity: "warning",
  },
} as const satisfies Record<string, PublicTerminalPresentationDefinition>;

export type PublicTerminalDetailCode =
  keyof typeof PUBLIC_TERMINAL_PRESENTATION_DEFINITIONS;

export function getPublicTerminalPresentationDefinition(
  detailCode: string,
): PublicTerminalPresentationDefinition | undefined {
  return Object.hasOwn(PUBLIC_TERMINAL_PRESENTATION_DEFINITIONS, detailCode)
    ? PUBLIC_TERMINAL_PRESENTATION_DEFINITIONS[
        detailCode as PublicTerminalDetailCode
      ]
    : undefined;
}

export function publicTerminalPresentation(detailCode: string):
  | (PublicTerminalPresentationDefinition & { message: string })
  | undefined {
  const definition = getPublicTerminalPresentationDefinition(detailCode);
  if (!definition) return undefined;
  return {
    ...definition,
    message: i18n.t(definition.messageKey, {
      defaultValue: definition.defaultMessage,
    }),
  };
}
