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
        parentId && "ml-4 border-l-2 border-l-neutral-300 pl-2 dark:border-l-neutral-600",
        (status === "running" || status === "complete" || status === "cancelled") && "border-neutral-200/60 bg-neutral-50/50 dark:border-neutral-700/40 dark:bg-neutral-800/30",
        status === "error" && "border-[var(--theme-danger-ring)] bg-[var(--theme-danger-soft)]",
        status === "pending" && "border-neutral-200/60 dark:border-neutral-700/40",
      )}
    >
      <Button
        type="text"
        htmlType="button"
        aria-label={`${name}: ${statusLabel}${parentId ? `，${t("assistantUi.subagent.nested")}` : ""}`}
        aria-haspopup="dialog"
        data-subagent-trigger={id}
        className="flex w-full cursor-pointer items-center gap-3 px-3.5 py-2.5 text-left transition-colors hover:bg-[color-mix(in_srgb,var(--theme-bg-card)_60%,transparent)] dark:hover:bg-[color-mix(in_srgb,var(--theme-bg-card)_5%,transparent)]"
        style={{ width: "100%", height: "auto", minHeight: 44, padding: "10px 14px", textAlign: "left", justifyContent: "flex-start", color: "inherit", background: "transparent" }}
        onClick={onOpen}
        onKeyDown={handleKeyDown}
      >
        <div className={clsx(
          "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg",
          status === "running" && "bg-[var(--theme-warning-soft)]",
          status === "complete" && "bg-[var(--theme-success-soft)]",
          status === "error" && "bg-[var(--theme-danger-soft)]",
          status === "cancelled" && "bg-[var(--theme-warning-soft)]",
          status === "pending" && "bg-neutral-500/10",
        )}>
          {status === "running" ? <Loader2 size={13} className="animate-spin text-[var(--theme-warning-text)]" />
            : status === "complete" ? <CheckCircle size={13} className="text-[var(--theme-success-text)]" />
              : status === "error" ? <XCircle size={13} className="text-[var(--theme-danger-text)]" />
                : status === "cancelled" ? <Ban size={13} className="text-[var(--theme-warning-text)]" />
                  : <ChevronRight size={13} className="text-neutral-400 dark:text-neutral-500" />}
        </div>
        <div className="min-w-0 flex-1">
          <span className={clsx(
            "block truncate text-[13px] font-medium",
            status === "error" ? "text-[var(--theme-danger-text)]" : "text-neutral-700 dark:text-neutral-300",
            status === "pending" && "text-neutral-600 dark:text-neutral-400",
          )}>{name}</span>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-neutral-500 dark:text-neutral-400">
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
