import { Button } from "@lobehub/ui/base-ui";
import { clsx } from "clsx";
import { useState } from "react";
import { Ban, CheckCircle, XCircle, ChevronRight } from "lucide-react";
import { Spin } from "@lobehub/ui/base-ui";

export type CollapsibleStatus =
  | "idle"
  | "loading"
  | "success"
  | "error"
  | "cancelled";
export type CollapsibleVariant = "default" | "tool" | "thinking" | "summary";

export interface CollapsiblePillProps {
  status?: CollapsibleStatus;
  icon: React.ReactNode;
  label: string;
  suffix?: React.ReactNode;
  defaultExpanded?: boolean;
  onExpandChange?: (expanded: boolean) => void;
  variant?: CollapsibleVariant;
  children?: React.ReactNode;
  expandable?: boolean;
  /** On mobile, call this instead of toggling inline expansion */
  onPanelOpen?: () => void;
  /** Add animated typing dots after label */
  animatedDots?: boolean;
  /** Preserve label text exactly instead of applying title formatting */
  formatLabel?: boolean;
}

// Get spinner color based on variant
function getSpinnerColor(variant: CollapsibleVariant): string {
  if (variant === "thinking") return "var(--theme-text-secondary)";
  return variant === "default" ? "var(--theme-success)" : "var(--theme-warning)";
}

// StatusIndicator component
function StatusIndicator({
  status,
  variant,
}: {
  status: CollapsibleStatus;
  variant: CollapsibleVariant;
}) {
  if (status === "loading") {
    return (
      <Spin
        size={12}
        className="shrink-0"
        style={{ color: getSpinnerColor(variant) }}
      />
    );
  }
  if (status === "success") {
    return <CheckCircle size={12} className="shrink-0" />;
  }
  if (status === "error") {
    return <XCircle size={12} className="shrink-0" />;
  }
  if (status === "cancelled") {
    return <Ban size={12} className="shrink-0" />;
  }
  // idle - no indicator
  return null;
}

// Get button styles based on status and variant
function getButtonStyles(
  status: CollapsibleStatus,
  variant: CollapsibleVariant,
): string {
  if (variant === "thinking") {
    return clsx(
      "bg-stone-200 dark:bg-stone-700",
      "text-stone-600 dark:text-stone-300",
      "hover:bg-stone-300 dark:hover:bg-stone-600",
    );
  }

  if (variant === "tool") {
    if (status === "loading") {
      return clsx(
        "bg-amber-100/80 dark:bg-amber-900/30",
        "text-amber-700 dark:text-amber-300",
      );
    }
    if (status === "success") {
      return clsx(
        "bg-emerald-100/80 dark:bg-emerald-900/30",
        "text-emerald-700 dark:text-emerald-300",
      );
    }
    if (status === "error") {
      return clsx(
        "bg-red-100/80 dark:bg-red-900/30",
        "text-red-700 dark:text-red-300",
      );
    }
    if (status === "cancelled") {
      return clsx(
        "bg-amber-100/80 dark:bg-amber-900/30",
        "text-amber-700 dark:text-amber-300",
      );
    }
    return clsx(
      "bg-stone-100 dark:bg-stone-800",
      "text-stone-600 dark:text-stone-400",
    );
  }

  if (variant === "summary") {
    if (status === "loading") {
      return clsx(
        "bg-amber-100/80 dark:bg-amber-900/30",
        "text-amber-700 dark:text-amber-300",
      );
    }
    if (status === "success") {
      return clsx(
        "bg-emerald-100/80 dark:bg-emerald-900/30",
        "text-emerald-700 dark:text-emerald-300",
      );
    }
    if (status === "error") {
      return clsx(
        "bg-red-100/80 dark:bg-red-900/30",
        "text-red-700 dark:text-red-300",
      );
    }
    if (status === "cancelled") {
      return clsx(
        "bg-amber-100/80 dark:bg-amber-900/30",
        "text-amber-700 dark:text-amber-300",
      );
    }
    return clsx(
      "bg-stone-100 dark:bg-stone-800",
      "text-stone-600 dark:text-stone-400",
    );
  }

  // default variant (for Sandbox)
  if (status === "error") {
    return clsx(
      "bg-red-100/80 dark:bg-red-900/30",
      "text-red-700 dark:text-red-300",
    );
  }
  if (status === "cancelled") {
    return clsx(
      "bg-amber-100/80 dark:bg-amber-900/30",
      "text-amber-700 dark:text-amber-300",
    );
  }
  return clsx(
    "bg-emerald-100/80 dark:bg-emerald-900/30",
    "text-emerald-700 dark:text-emerald-300",
  );
}

export function CollapsiblePill({
  status = "idle",
  icon,
  label,
  suffix,
  defaultExpanded = false,
  onExpandChange,
  variant = "default",
  children,
  expandable = true,
  onPanelOpen,
  animatedDots = false,
  formatLabel = true,
}: CollapsiblePillProps) {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
  const hasChildren = children !== undefined;

  const handleToggle = () => {
    if (!expandable && !hasChildren) return;
    if (onPanelOpen) {
      onPanelOpen();
      return;
    }
    const newState = !isExpanded;
    setIsExpanded(newState);
    onExpandChange?.(newState);
  };

  const canExpand = expandable || hasChildren;

  // Format label: capitalize first letter and convert underscores to spaces
  const formattedLabel = label
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
  const displayedLabel = formatLabel ? formattedLabel : label;

  const content = (
    <>
      <StatusIndicator status={status} variant={variant} />
      {icon}
      <span className={clsx("min-w-0 break-words", animatedDots && "typing-dots")}>{displayedLabel}</span>
      {suffix}
      {canExpand && <ChevronRight size={14} className={clsx("shrink-0 transition-transform", isExpanded && "rotate-90")} />}
    </>
  );
  const rowClassName = "inline-flex max-w-full flex-wrap items-center gap-2 rounded-lg px-3 py-2 text-[13px] leading-5";
  return (
    <div className="my-1 min-w-0 max-w-full" data-chat-activity-status={status}>
      <div className={clsx("w-fit max-w-full rounded-lg", getButtonStyles(status, variant))}>
        {canExpand ? (
          <Button type="text" htmlType="button" onClick={handleToggle} aria-expanded={isExpanded}
            className={rowClassName} style={{ color: "inherit", background: "transparent", height: "auto", fontSize: 13, padding: "8px 12px" }}>
            {content}
          </Button>
        ) : <div className={rowClassName}>{content}</div>}
      </div>
      {isExpanded && hasChildren && (
        <div className="mt-1 animate-[fade-in_150ms_ease-out]">{children}</div>
      )}
    </div>
  );
}
