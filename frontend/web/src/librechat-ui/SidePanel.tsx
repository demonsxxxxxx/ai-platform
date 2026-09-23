import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { workbenchSurface } from "../components/workbench/workbenchSurface";

export interface LibreChatSidePanelProps {
  additionalSections?: ReactNode;
}

/** Hosts the workbench's authorized context surfaces. */
export function LibreChatSidePanel({
  additionalSections,
}: LibreChatSidePanelProps) {
  const { t } = useTranslation();

  return (
    <aside
      data-librechat-side-panel
      className="flex h-full min-h-0 flex-col bg-[var(--theme-workbench-canvas)] px-4 py-3"
    >
      <section
        data-librechat-context-overview
        aria-labelledby="librechat-context-overview-label"
        className="min-h-0 flex-1 overflow-y-auto pr-1"
      >
        <h2
          id="librechat-context-overview-label"
          className={workbenchSurface.label}
        >
          {t("workbench.workspaceContext", "Workspace context")}
        </h2>
        {additionalSections}
      </section>
    </aside>
  );
}
