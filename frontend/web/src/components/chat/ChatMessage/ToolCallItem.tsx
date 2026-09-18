import { Ban, CircleX, FileText, Globe, Pencil, Search, Sparkles, Terminal, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";
import { CollapsiblePill, LoadingSpinner } from "../../common";
import type { CollapsibleStatus } from "../../common";
import { ToolResultContent } from "./items/McpBlockPreview";
import { openPersistentToolPanel } from "./items/persistentToolPanelState";

// Re-export all sub-components
export { ReadFileItem } from "./items/ReadFileItem";
export { EditFileItem } from "./items/EditFileItem";
export { WriteFileItem } from "./items/WriteFileItem";
export { GrepItem } from "./items/GrepItem";
export { LsItem } from "./items/LsItem";
export { GlobItem } from "./items/GlobItem";

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
  name,
  args,
  result,
  success,
  status: toolStatus,
  isPending,
  cancelled,
  publicCategory,
  publicOperationId,
  durationMs,
}: {
  name: string;
  args: Record<string, unknown>;
  result?: string | Record<string, unknown>;
  success?: boolean;
  status?: "started" | "completed" | "failed" | "denied";
  isPending?: boolean;
  cancelled?: boolean;
  publicCategory?: string;
  publicOperationId?: string;
  durationMs?: number;
}) {
  const { t } = useTranslation();
  const isPublicTool = Boolean(publicOperationId && publicCategory);
  const hasResult = !isPublicTool && result !== undefined;

  // Legacy history may still contain the old server:tool naming convention.
  const colonIdx = isPublicTool ? -1 : name.indexOf(":");
  const isMcpTool = colonIdx > 0;
  const serverName = isMcpTool ? name.substring(0, colonIdx) : null;
  const toolName = isMcpTool ? name.substring(colonIdx + 1) : name;
  const formattedToolName = toolName
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");

  const displayArgs = isPublicTool
    ? {}
    : (() => {
        if (args.partial !== undefined) {
          try {
            return JSON.parse(args.partial as string);
          } catch {
            return { partial: args.partial };
          }
        }
        return args;
      })();

  const hasArgs = !isPublicTool && Object.keys(displayArgs).length > 0;

  let status: CollapsibleStatus = "idle";
  const lifecycleStatus =
    toolStatus ||
    (isPending ? "started" : cancelled ? "denied" : success ? "completed" : hasResult ? "failed" : undefined);
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
  const durationLabel = isPublicTool ? formatDurationMs(durationMs) : null;
  const ToolIcon = isPublicTool
    ? publicCategoryIcon(publicCategory!)
    : lifecycleStatus === "denied"
      ? Ban
      : lifecycleStatus === "failed"
        ? CircleX
        : isMcpTool
          ? Globe
          : Wrench;

  const publicCategoryLabel = publicCategory
    ? PUBLIC_CATEGORY_LABELS[publicCategory] || "工具"
    : null;
  const canExpand = !isPublicTool && (hasArgs || hasResult);

  const panelContent = canExpand && (
    <div className="space-y-3 max-h-full overflow-y-auto p-2 sm:p-4 [&_pre]:!text-sm">
      {hasArgs && (
        <div className="p-3 sm:p-4 rounded-lg sm:rounded-xl bg-stone-100 dark:bg-stone-700/50">
          <div className="text-xs uppercase tracking-wider text-stone-400 dark:text-stone-500 mb-2 font-medium">
            {t("chat.message.args")}
          </div>
          <pre className="text-sm text-stone-600 dark:text-stone-300 overflow-x-auto overflow-y-auto min-w-0 font-mono">
            {JSON.stringify(displayArgs, null, 2)}
          </pre>
        </div>
      )}

      {hasResult && (
        <div className="p-3 sm:p-4 rounded-lg sm:rounded-xl bg-stone-100 dark:bg-stone-700/50">
          <div className="text-xs uppercase tracking-wider text-stone-400 dark:text-stone-500 mb-2 font-medium">
            {t("chat.message.result")}
          </div>
          <ToolResultContent result={result} />
        </div>
      )}

      {isPending && (
        <div className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
          <LoadingSpinner size="xs" />
          <span>{t("chat.message.running")}</span>
        </div>
      )}
    </div>
  );

  return (
    <>
      <CollapsiblePill
        status={status}
        icon={<ToolIcon size={12} aria-hidden="true" className="shrink-0 opacity-70" />}
        label={isPublicTool && publicCategoryLabel
          ? `${publicCategoryLabel}：${name}`
          : toolName}
        suffix={
          serverName || statusLabel || durationLabel || publicCategoryLabel ? (
            <span className="flex min-w-0 items-center gap-1.5">
              {serverName && (
                <span className="text-[9px] px-1.5 py-0.5 rounded-md bg-white/30 dark:bg-black/20 opacity-70 font-medium truncate max-w-[120px]">
                  {serverName}
                </span>
              )}
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
        formatLabel={!isPublicTool}
        expandable={canExpand}
        onPanelOpen={() => {
          if (!canExpand) return;
          openPersistentToolPanel({
            title: formattedToolName,
            icon: <ToolIcon size={16} aria-hidden="true" />,
            status,
            subtitle: publicCategoryLabel || serverName || undefined,
            children: panelContent,
          });
        }}
      >
        {canExpand && (
          <div className="mt-2 ml-4 pl-3 border-l-2 border-stone-200/60 dark:border-stone-700/50 space-y-2 max-h-96 overflow-y-auto min-w-0">
            {hasArgs && (
              <div className="p-2 rounded-md bg-stone-50/80 dark:bg-stone-800/50">
                <div className="text-xs uppercase tracking-wider text-stone-400 dark:text-stone-500 mb-1 font-medium">
                  {t("chat.message.args")}
                </div>
                <pre className="text-xs text-stone-600 dark:text-stone-300 overflow-x-auto max-h-40 overflow-y-auto min-w-0">
                  {JSON.stringify(displayArgs, null, 2)}
                </pre>
              </div>
            )}

            {hasResult && (
              <div className="p-2 rounded-md bg-stone-50/80 dark:bg-stone-800/50">
                <div className="text-xs uppercase tracking-wider text-stone-400 dark:text-stone-500 mb-1 font-medium">
                  {t("chat.message.result")}
                </div>
                <ToolResultContent result={result} />
              </div>
            )}

            {isPending && (
              <div className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
                <LoadingSpinner size="xs" />
                <span>{t("chat.message.running")}</span>
              </div>
            )}
          </div>
        )}
      </CollapsiblePill>
    </>
  );
}
