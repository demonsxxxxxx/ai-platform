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

const STATUS_ICON_COLORS = {
  success: "text-[var(--theme-success-text)]",
  error: "text-[var(--theme-danger-text)]",
  cancelled: "text-[var(--theme-warning-text)]",
} as const;

/** Public tool activity layout adapted from the pinned ToolCall element. */
export function ToolCall({ status, icon, label, suffix, errorKind }: ToolCallProps) {
  const StatusIcon = STATUS_ICONS[status as keyof typeof STATUS_ICONS];
  return (
    <div className="my-1 min-w-0 max-w-full" data-chat-activity-status={status} data-slot="tool-call">
      <div
        className={clsx(
          "inline-flex w-fit max-w-full flex-wrap items-center gap-2 px-2 py-1 text-[13px] leading-5",
          // Status color stays on the icon; only errors also color the text.
          status === "error" ? "text-[var(--theme-danger-text)]" : "text-[var(--theme-text-secondary)]",
        )}
      >
        {status === "loading" ? <LoaderCircle size={13} aria-hidden="true" className="shrink-0 animate-spin motion-reduce:animate-none" /> : StatusIcon ? <StatusIcon size={13} aria-hidden="true" className={clsx("shrink-0", STATUS_ICON_COLORS[status as keyof typeof STATUS_ICON_COLORS])} /> : null}
        {icon}
        <span className="min-w-0 break-words">{label}</span>
        {suffix}
        {errorKind && <ToolError kind={errorKind} />}
      </div>
    </div>
  );
}
