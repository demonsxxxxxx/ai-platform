import { Button } from "@lobehub/ui/base-ui";
import { ChevronRight, LoaderCircle } from "lucide-react";
import type { ReactNode } from "react";
import { clsx } from "clsx";

export type RunActivityStatus = "running" | "stopping" | "complete" | "error";

export interface RunActivityProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  status: RunActivityStatus;
  label: string;
  itemCount: ReactNode;
  toggleLabel: string;
  issueSummary?: ReactNode;
  contentId: string;
  ariaControls?: string;
  children: ReactNode;
  showTrigger?: boolean;
}

/**
 * Public work disclosure in the ghost style of assistant-ui's ToolGroup
 * (727a02dc): one inline muted trigger whose chevron follows the label, and an
 * indented trace under a hairline. No separate right-aligned toggle text.
 */
export function RunActivity({
  open,
  onOpenChange,
  status,
  label,
  itemCount,
  toggleLabel,
  issueSummary,
  contentId,
  ariaControls,
  children,
  showTrigger = true,
}: RunActivityProps) {
  const running = status === "running";
  return (
    <div data-slot="run-activity" data-status={status}>
      {showTrigger && (
        <Button
          type="text"
          htmlType="button"
          data-message-work-details-toggle
          aria-expanded={open}
          aria-controls={ariaControls ?? contentId}
          title={toggleLabel}
          onClick={() => onOpenChange(!open)}
          className="run-activity-trigger"
          style={{ height: "auto", padding: "4px 0", background: "transparent", color: "var(--theme-text-secondary)" }}
        >
          <span className="inline-flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-left text-sm">
            {running ? (
              <LoaderCircle size={13} aria-hidden="true" className="shrink-0 animate-spin motion-reduce:animate-none" />
            ) : null}
            <span className={clsx("font-medium", running && "aui-shimmer")}>{label}</span>
            <span className="text-[var(--theme-text-tertiary)]" aria-hidden="true">·</span>
            <span>{itemCount}</span>
            {issueSummary}
            <ChevronRight
              size={14}
              aria-hidden="true"
              className={clsx("shrink-0 transition-transform duration-200 motion-reduce:transition-none", open && "rotate-90")}
            />
          </span>
        </Button>
      )}
      <div
        id={contentId}
        hidden={!open}
        className="ml-[6px] mt-1 border-l border-[var(--theme-border)] pl-4"
      >
        {children}
      </div>
    </div>
  );
}
