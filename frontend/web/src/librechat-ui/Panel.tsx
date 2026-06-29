import type { ReactNode } from "react";

interface LibreChatPanelSectionProps {
  group: string;
  label: string;
  children: ReactNode;
}

/** Groups expanded sidebar items under a LibreChat-style panel label. */
export function LibreChatPanelSection({
  group,
  label,
  children,
}: LibreChatPanelSectionProps) {
  return (
    <div
      data-workbench-nav-group={group}
      data-librechat-expanded-panel={group}
      className="space-y-0.5"
    >
      <p className="px-[9px] pb-1 pt-1 text-[11px] font-semibold tracking-normal text-[var(--theme-text-tertiary)]">
        {label}
      </p>
      {children}
    </div>
  );
}
