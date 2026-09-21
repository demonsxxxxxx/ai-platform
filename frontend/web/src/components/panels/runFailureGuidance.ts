import type { AdminRunDetailResponse } from "../../services/api/adminRuns";
import type { FailureGuidance } from "../../types/failureGuidance";

const COMPLETED_STEP_STATUSES = new Set([
  "completed",
  "reused",
  "succeeded",
  "success",
]);

function normalized(value: string | null | undefined): string {
  return value?.trim().toLowerCase() ?? "";
}

export function buildAdminFailureGuidance(
  detail: AdminRunDetailResponse,
): FailureGuidance | null {
  const status = normalized(detail.run.status);
  if (status !== "failed" && status !== "cancelled") return null;

  const completedStepCount = detail.steps.filter((step) =>
    COMPLETED_STEP_STATUSES.has(normalized(step.status)),
  ).length;
  const retainedItems = [
    completedStepCount ? `${completedStepCount} 个已完成步骤` : "",
    detail.artifacts.length ? `${detail.artifacts.length} 个已登记文件` : "",
  ].filter(Boolean);
  const errorCode = normalized(detail.run.error_code);
  const uncertainToolOutcome = [
    "mcp_execution_outcome_unknown",
    "mcp_execution_succeeded_receipt_incomplete",
    "tool_execution_outcome_unconfirmed",
  ].includes(errorCode);
  const permissionFailure =
    errorCode.includes("not_authorized") ||
    errorCode.includes("permission") ||
    errorCode.includes("denied");
  const deliveryFailure = errorCode === "terminal_reconciliation_failed";

  let nextAction =
    "可以重试；如问题持续，请联系管理员并提供问题编号。";
  if (uncertainToolOutcome) {
    nextAction =
      "请勿直接重试，以免重复执行外部操作；先按问题编号核对工具回执。";
  } else if (permissionFailure) {
    nextAction = "请检查账号、专家和工具授权；修复权限后再重新提交。";
  } else if (deliveryFailure) {
    nextAction = detail.artifacts.length
      ? "先查看已登记文件，再重试结果同步；不要重新执行已完成的主体工作。"
      : "先重试结果同步；如仍失败，请按问题编号排查终态写入。";
  } else if (detail.artifacts.length) {
    nextAction = "先查看已登记文件，再决定是否需要重试剩余工作。";
  }

  const whatHappened = uncertainToolOutcome
    ? "工具执行结果尚未确认，系统无法安全判断外部操作是否已经完成。"
    : permissionFailure
      ? "当前账号或任务所需能力未通过权限检查。"
      : deliveryFailure
        ? "主体执行已经结束，但最终结果同步失败。"
        : detail.run.error_message?.trim() ||
          (status === "cancelled" ? "任务已取消。" : "任务未能完成。");

  return {
    whatHappened,
    retained: retainedItems.length
      ? `已保留 ${retainedItems.join("、")}。`
      : "未记录到可确认的已完成步骤或文件。",
    nextAction,
    problemNumber: detail.run.run_id || null,
  };
}
