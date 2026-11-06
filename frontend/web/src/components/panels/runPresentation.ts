import type { AdminRunSummary } from "../../services/api/adminRuns";
import { formatRunDuration } from "./runPerformance";

export const RUN_STATUS_LABELS: Record<string, string> = {
  queued: "排队", running: "执行中", succeeded: "成功", failed: "失败",
  cancelled: "已取消", cancel_requested: "取消中", denied: "已拒绝",
};
export const STATUS_TONES: Record<string, string> = {
  queued: "bg-[var(--theme-warning-soft)] text-[var(--theme-warning)] ring-[var(--theme-warning-ring)]",
  running: "bg-[var(--theme-info-soft)] text-[var(--theme-info)] ring-[var(--theme-info-ring)]",
  succeeded: "bg-[var(--theme-success-soft)] text-[var(--theme-success)] ring-[var(--theme-success-ring)]",
  failed: "bg-[var(--theme-danger-soft)] text-[var(--theme-danger-text)] ring-[var(--theme-danger-ring)]",
  cancelled: "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-[var(--theme-border)]",
  cancel_requested: "bg-[var(--theme-warning-soft)] text-[var(--theme-warning)] ring-[var(--theme-warning-ring)]",
};
export const displayText = (value?: string | null) => value?.trim() ?? "";
export const runDisplayTitle = (run: AdminRunSummary) => displayText(run.session_title) || displayText(run.task_summary) || "未命名任务";
export const userDisplayName = (run: AdminRunSummary) => displayText(run.user_display_name) || "未知用户";
export const workspaceDisplayName = (run: AdminRunSummary) => displayText(run.workspace_name) || "未知工作区";
export const agentDisplayName = (run: AdminRunSummary) => displayText(run.agent_name) || "未命名 Agent";
export const skillDisplayName = (run: AdminRunSummary) => displayText(run.skill_name) || (run.execution_kind === "harness_chat" ? "通用对话" : "未命名 Skill");
export function compactIdentifier(value?: string | null) {
  const text = displayText(value);
  return !text ? "未记录" : text.length <= 18 ? text : `${text.slice(0, 8)}…${text.slice(-6)}`;
}
export function dateTime(value?: string | null) {
  if (!value || !Number.isFinite(Date.parse(value))) return "未记录";
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) + " 北京时间";
}
export const statusLabel = (status?: string | null) => RUN_STATUS_LABELS[status ?? ""] ?? status ?? "未知";
export function durationLabel(run: AdminRunSummary) {
  if (run.latency?.total_ms != null) return formatRunDuration(run.latency.total_ms);
  return run.status === "queued" ? "排队中" : ["running", "cancel_requested"].includes(run.status) ? "执行中" : "未采集";
}
export function tokenCountLabel(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? new Intl.NumberFormat("zh-CN").format(value) : "未记录";
}
export function estimatedCostLabel(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? `${new Intl.NumberFormat("zh-CN").format(value)} 最小计价单位` : "未记录";
}
export function byteSizeLabel(value: number) {
  if (!Number.isFinite(value) || value < 0) return "未记录";
  return value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1048576).toFixed(1)} MB`;
}
