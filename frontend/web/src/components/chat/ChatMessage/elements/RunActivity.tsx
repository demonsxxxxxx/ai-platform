import { Button } from "@lobehub/ui/base-ui";
import { ChevronDown, ListTree } from "lucide-react";
import type { ReactNode } from "react";
import { clsx } from "clsx";

export type RunActivityStatus = "running" | "complete" | "error";

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

/** Public work disclosure adapted from assistant-ui's RunActivity structure. */
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
  return (
    <div data-slot="run-activity" data-status={status}>
      {showTrigger && (
        <Button
          type="text"
          htmlType="button"
          data-message-work-details-toggle
          aria-expanded={open}
          aria-controls={ariaControls ?? contentId}
          onClick={() => onOpenChange(!open)}
          className="flex h-8 w-full max-w-2xl items-center gap-2 text-left text-sm font-medium text-[var(--theme-text-secondary)] transition-colors hover:text-[var(--theme-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)] focus-visible:ring-offset-2"
          style={{ color: "var(--theme-text-secondary)", background: "transparent", height: 32, padding: "0 8px" }}
        >
          <ListTree size={15} aria-hidden="true" />
          <span>{label}</span>
          <span className="text-xs font-normal opacity-70">{itemCount}</span>
          {issueSummary}
          <span className="ml-auto text-xs font-normal">{toggleLabel}</span>
          <ChevronDown
            size={15}
            aria-hidden="true"
            className={clsx("shrink-0 transition-transform duration-200", open && "rotate-180")}
          />
        </Button>
      )}
      <div id={contentId} hidden={!open}>
        {children}
      </div>
    </div>
  );
}
