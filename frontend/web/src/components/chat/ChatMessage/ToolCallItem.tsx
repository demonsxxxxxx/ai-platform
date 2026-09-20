import { FileText, Globe, Pencil, Search, Sparkles, Terminal, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";
import { CollapsiblePill } from "../../common";
import type { CollapsibleStatus } from "../../common";
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
  publicCategory,
  publicDisplayName,
  publicOperationId,
  durationMs,
}: {
  status?: "started" | "completed" | "failed" | "denied";
  publicCategory?: string;
  publicDisplayName?: string;
  publicOperationId?: string;
  durationMs?: number;
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

  let status: CollapsibleStatus = "idle";
  if (lifecycleStatus === "started") {
    status = "loading";
  } else if (lifecycleStatus === "denied") {
    status = "cancelled";
  } else if (lifecycleStatus === "completed") {
    status = "success";
  } else if (lifecycleStatus === "failed") {
    status = "error";
  }
  const statusLabel = lifecycleStatus === "started"
    ? t("chat.runStatus.event.toolCallStarted", { defaultValue: "操作已开始" })
    : lifecycleStatus === "completed"
      ? t("chat.runStatus.event.toolCallCompleted", { defaultValue: "操作已完成" })
      : lifecycleStatus === "denied"
        ? t("chat.runStatus.event.toolPermissionDenied", { defaultValue: "操作未获授权" })
        : lifecycleStatus === "failed"
          ? t("chat.runStatus.status.failed", { defaultValue: "失败" })
          : null;
  const durationLabel = formatDurationMs(durationMs);
  const ToolIcon = publicCategoryIcon(publicCategory);
  const publicCategoryLabel = PUBLIC_CATEGORY_LABELS[publicCategory] || "工具";

  return (
    <CollapsiblePill
      status={status}
      icon={<ToolIcon size={12} aria-hidden="true" className="shrink-0 opacity-70" />}
      label={`${publicCategoryLabel}：${canonicalDisplayName}`}
      suffix={
        statusLabel || durationLabel ? (
          <span className="flex min-w-0 items-center gap-1.5">
            {durationLabel && (
              <span className="text-[10px] font-medium">{durationLabel}</span>
            )}
            {statusLabel && (
              <span role="status" aria-label={statusLabel} className="text-[10px] font-medium">
                {statusLabel}
              </span>
            )}
          </span>
        ) : undefined
      }
      variant="tool"
      formatLabel={false}
      expandable={false}
    />
  );
}
