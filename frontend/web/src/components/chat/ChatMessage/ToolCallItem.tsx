import { FileText, Globe, Pencil, Search, Sparkles, Terminal, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ToolCall } from "./elements/ToolCall";
import type { ToolErrorKind } from "./elements/ToolError";
import {
  getPublicToolDisplayName,
  isPublicToolPresentation,
} from "./messagePartVisibility";

const PUBLIC_CATEGORY_LABELS: Readonly<Record<string, string>> = {
  skill: "使用 Skill",
  mcp: "调用 MCP 工具",
  read: "读取",
  write: "写入",
  edit: "编辑",
  search: "搜索",
  execute: "执行",
};

function formatDurationMs(durationMs: number | undefined): string | null {
  if (
    typeof durationMs !== "number" ||
    !Number.isInteger(durationMs) ||
    durationMs < 0
  ) {
    return null;
  }
  return durationMs < 1000
    ? `${durationMs}毫秒`
    : `${(durationMs / 1000).toFixed(2)}秒`;
}

function publicCategoryIcon(category: string) {
  switch (category) {
    case "read":
      return FileText;
    case "write":
    case "edit":
      return Pencil;
    case "search":
      return Search;
    case "execute":
      return Terminal;
    case "skill":
      return Sparkles;
    case "mcp":
      return Globe;
    default:
      return Wrench;
  }
}

// Collapsible Tool Call Item (compact design)
export function ToolCallItem({
  status: lifecycleStatus,
  cancelled,
  publicCategory,
  publicDisplayName,
  publicOperationId,
  durationMs,
  showDuration = false,
}: {
  status?: "started" | "completed" | "failed" | "denied";
  cancelled?: boolean;
  publicCategory?: string;
  publicDisplayName?: string;
  publicOperationId?: string;
  durationMs?: number;
  showDuration?: boolean;
}) {
  const { t } = useTranslation();
  const canonicalDisplayName = getPublicToolDisplayName(
    publicCategory,
    publicDisplayName,
  );
  if (
    !publicCategory ||
    !canonicalDisplayName ||
    publicDisplayName !== canonicalDisplayName ||
    !isPublicToolPresentation(
      publicOperationId,
      publicCategory,
      lifecycleStatus,
      durationMs,
    )
  ) {
    return null;
  }

  const isStopped = lifecycleStatus === "started" && cancelled === true;
  let status: "idle" | "loading" | "success" | "error" | "cancelled" = "idle";
  let errorKind: ToolErrorKind | undefined;
  if (isStopped) {
    status = "cancelled";
    errorKind = "cancelled";
  } else if (lifecycleStatus === "started") {
    status = "loading";
  } else if (lifecycleStatus === "denied") {
    status = "cancelled";
    errorKind = "denied";
  } else if (lifecycleStatus === "completed") {
    status = "success";
  } else if (lifecycleStatus === "failed") {
    status = "error";
    errorKind = "failed";
  }
  const statusLabel = isStopped
    ? undefined
    : lifecycleStatus === "started"
      ? t("chat.runStatus.event.toolCallStarted", { defaultValue: "操作已开始" })
      : lifecycleStatus === "completed"
        ? t("chat.runStatus.event.toolCallCompleted", { defaultValue: "操作已完成" })
        : undefined;
  const durationLabel = showDuration ? formatDurationMs(durationMs) : null;
  const ToolIcon = publicCategoryIcon(publicCategory);
  const publicCategoryLabel = PUBLIC_CATEGORY_LABELS[publicCategory] || "工具";

  return (
    <ToolCall
      status={status}
      icon={<ToolIcon size={14} aria-hidden="true" className="shrink-0 opacity-80" />}
      label={`${publicCategoryLabel}：${canonicalDisplayName}`}
      errorKind={errorKind}
      suffix={
        statusLabel || durationLabel ? (
          <span className="text-xs font-medium">
            {durationLabel ? <span className="mr-1.5 tabular-nums">{durationLabel}</span> : null}
            {statusLabel}
          </span>
        ) : undefined
      }
    />
  );
}
