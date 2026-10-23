import { type ReactNode } from "react";
import { type LucideIcon } from "lucide-react";

/* ── FileIcon ── */

interface FileIconProps {
  icon: LucideIcon;
  bg?: string;
  color?: string;
}

export function FileIcon({
  icon: Icon,
  bg = "bg-[var(--theme-info-soft)]",
  color = "text-[var(--theme-info-text)]",
}: FileIconProps) {
  return (
    <div
      className={`flex items-center justify-center size-10 rounded-lg shrink-0 ${bg}`}
    >
      <Icon size={18} className={color} />
    </div>
  );
}

/* ── PreviewHeader ── */

type PreviewHeaderVariant = "sidebar" | "card";

interface PreviewHeaderProps {
  icon: LucideIcon;
  iconBg?: string;
  iconColor?: string;
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  variant?: PreviewHeaderVariant;
}
export function PreviewHeader({
  icon,
  iconBg,
  iconColor,
  title,
  subtitle,
  actions,
  variant = "sidebar",
}: PreviewHeaderProps) {
  const isSidebar = variant === "sidebar";

  return (
    <div
      className={`flex items-center ${
        isSidebar
          ? "gap-2.5 px-3 sm:px-4 py-2.5 sm:py-3"
          : "gap-2 sm:gap-3 px-2 sm:px-4 py-2 sm:py-3"
      } border-b border-neutral-200 dark:border-neutral-700/80 shrink-0 ${
        isSidebar
          ? "bg-neutral-50 dark:bg-[var(--theme-bg-card)]"
          : "bg-neutral-50/80 dark:bg-neutral-800/60"
      } whitespace-nowrap`}
    >
      <FileIcon icon={icon} bg={iconBg} color={iconColor} />
      <div
        className={`flex-1 min-w-0 ${
          isSidebar ? "min-w-[120px] sm:min-w-[180px]" : ""
        }`}
      >
        <h3
          className={`${
            isSidebar
              ? "text-[13px] sm:text-sm font-medium"
              : "font-medium text-sm"
          } text-neutral-800 dark:text-neutral-100 truncate`}
          title={title}
        >
          {title}
        </h3>
        {subtitle && (
          <p
            className={`text-xs ${
              isSidebar ? "" : "hidden sm:block"
            } text-neutral-400 dark:text-neutral-500 mt-0.5`}
          >
            {subtitle}
          </p>
        )}
      </div>
      {actions && (
        <div className="flex items-center gap-0.5 sm:gap-1 relative z-10 shrink-0">
          {actions}
        </div>
      )}
    </div>
  );
}
