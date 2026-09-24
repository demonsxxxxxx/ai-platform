import type { ReactNode } from "react";

export interface LibreChatSidePanelProps {
  additionalSections?: ReactNode;
}

/** Hosts the workbench's authorized context surfaces. */
export function LibreChatSidePanel({
  additionalSections,
}: LibreChatSidePanelProps) {
  return (
    <aside
      data-librechat-side-panel
      className="flex h-full min-h-0 flex-col bg-[var(--theme-workbench-canvas)] px-4 py-3"
    >
      <div
        data-librechat-context-overview
        className="min-h-0 flex-1 overflow-y-auto pr-1"
      >
        {additionalSections}
      </div>
    </aside>
  );
}
