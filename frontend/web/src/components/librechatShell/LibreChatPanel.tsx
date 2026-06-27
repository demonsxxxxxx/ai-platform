import type { ReactNode } from "react";

interface LibreChatPanelSectionProps {
  group: string;
  label: string;
  children: ReactNode;
}

export function LibreChatPanelSection({
  group,
  label,
  children,
}: LibreChatPanelSectionProps) {
  return (
    <div
      data-workbench-nav-group={group}
      data-librechat-expanded-panel={group}
      className="space-y-1"
    >
      <p className="px-[9px] pb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </p>
      {children}
    </div>
  );
}
