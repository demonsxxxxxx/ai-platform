import { Ban, CheckCircle, LoaderCircle, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { clsx } from "clsx";
import type { CollapsibleStatus } from "../../../common";
import { ToolError, type ToolErrorKind } from "./ToolError";

export interface ToolCallProps {
  status: CollapsibleStatus;
  icon: ReactNode;
  label: string;
  suffix?: ReactNode;
  errorKind?: ToolErrorKind;
}

const STATUS_ICONS = {
  loading: LoaderCircle,
  success: CheckCircle,
  error: XCircle,
  cancelled: Ban,
} as const;

/** Public tool activity layout adapted from the pinned ToolCall element. */
export function ToolCall({ status, icon, label, suffix, errorKind }: ToolCallProps) {
  const StatusIcon = STATUS_ICONS[status as keyof typeof STATUS_ICONS];
  return (
    <div className="my-1 min-w-0 max-w-full" data-chat-activity-status={status} data-slot="tool-call">
      <div
        className={clsx(
          "inline-flex w-fit max-w-full flex-wrap items-center gap-2 rounded-lg px-3 py-2 text-[13px] leading-5",
          status === "loading" && "bg-amber-100/80 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",
          status === "success" && "bg-emerald-100/80 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300",
          status === "error" && "bg-red-100/80 text-red-700 dark:bg-red-900/30 dark:text-red-300",
          status === "cancelled" && "bg-amber-100/80 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",
          status === "idle" && "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-400",
        )}
      >
        {status === "loading" ? <LoaderCircle size={12} aria-hidden="true" className="shrink-0 animate-spin motion-reduce:animate-none" /> : StatusIcon ? <StatusIcon size={12} aria-hidden="true" className="shrink-0" /> : null}
        {icon}
        <span className="min-w-0 break-words">{label}</span>
        {suffix}
        {errorKind && <ToolError kind={errorKind} />}
      </div>
    </div>
  );
}
