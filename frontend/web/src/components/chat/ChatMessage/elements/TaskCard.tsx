import { Button } from "@lobehub/ui/base-ui";
import { Ban, CheckCircle, ChevronRight, Loader2, XCircle } from "lucide-react";
import type { ReactNode, KeyboardEvent } from "react";
import { clsx } from "clsx";
import { useTranslation } from "react-i18next";

export type TaskCardStatus = "pending" | "running" | "complete" | "error" | "cancelled";

export interface TaskCardProps {
  id: string;
  parentId?: string;
  name: string;
  status: TaskCardStatus;
  statusLabel: string;
  currentCategory?: string;
  progressPercent?: number;
  durationMs?: number;
  onOpen: () => void;
  children?: ReactNode;
}

/** TaskCard adapted from the pinned subagent/task activity structures. */
export function TaskCard({
  id,
  parentId,
  name,
  status,
  statusLabel,
  currentCategory,
  progressPercent,
  durationMs,
  onOpen,
  children,
}: TaskCardProps) {
  const { t } = useTranslation();
  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onOpen();
  };

  return (
    <div
      data-slot="task-card"
      data-subagent-id={id}
      data-parent-agent-id={parentId}
      className={clsx(
        "my-1.5 min-w-0 overflow-hidden rounded-xl border transition-all duration-200",
        parentId && "ml-4 border-l-2 border-l-stone-300 pl-2 dark:border-l-stone-600",
        (status === "running" || status === "complete" || status === "cancelled") && "border-stone-200/60 bg-stone-50/50 dark:border-stone-700/40 dark:bg-stone-800/30",
        status === "error" && "border-red-200/60 bg-red-50/60 dark:border-red-900/40 dark:bg-red-950/20",
        status === "pending" && "border-stone-200/60 dark:border-stone-700/40",
      )}
    >
      <Button
        type="text"
        htmlType="button"
        aria-label={`${name}: ${statusLabel}${parentId ? `，${t("assistantUi.subagent.nested")}` : ""}`}
        aria-haspopup="dialog"
        data-subagent-trigger={id}
        className="flex w-full cursor-pointer items-center gap-3 px-3.5 py-2.5 text-left transition-colors hover:bg-white/60 dark:hover:bg-white/5"
        style={{ width: "100%", height: "auto", minHeight: 44, padding: "10px 14px", textAlign: "left", justifyContent: "flex-start", color: "inherit", background: "transparent" }}
        onClick={onOpen}
        onKeyDown={handleKeyDown}
      >
        <div className={clsx(
          "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg",
          status === "running" && "bg-amber-500/10",
          status === "complete" && "bg-emerald-500/10",
          status === "error" && "bg-red-500/10",
          status === "cancelled" && "bg-amber-500/10",
          status === "pending" && "bg-stone-500/10",
        )}>
          {status === "running" ? <Loader2 size={13} className="animate-spin text-amber-500 dark:text-amber-400" />
            : status === "complete" ? <CheckCircle size={13} className="text-emerald-500 dark:text-emerald-400" />
              : status === "error" ? <XCircle size={13} className="text-red-500 dark:text-red-400" />
                : status === "cancelled" ? <Ban size={13} className="text-amber-500 dark:text-amber-400" />
                  : <ChevronRight size={13} className="text-stone-400 dark:text-stone-500" />}
        </div>
        <div className="min-w-0 flex-1">
          <span className={clsx(
            "block truncate text-[13px] font-medium",
            status === "error" ? "text-red-700 dark:text-red-300" : "text-stone-700 dark:text-stone-300",
            status === "pending" && "text-stone-600 dark:text-stone-400",
          )}>{name}</span>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-stone-500 dark:text-stone-400">
            <span data-subagent-status>{statusLabel}</span>
            {parentId && <span data-subagent-nested-label>{t("assistantUi.subagent.nested")}</span>}
            {currentCategory && <span>{t("assistantUi.subagent.category", { category: t(`assistantUi.toolCategory.${currentCategory}`) })}</span>}
            {progressPercent !== undefined && <span>{t("assistantUi.subagent.progress", { percent: progressPercent })}</span>}
            {durationMs !== undefined && <span>{t("assistantUi.subagent.duration", { seconds: (durationMs / 1000).toFixed(1) })}</span>}
          </div>
        </div>
      </Button>
      {children}
    </div>
  );
}
