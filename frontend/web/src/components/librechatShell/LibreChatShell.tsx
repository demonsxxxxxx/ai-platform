import { useState, type ReactNode } from "react";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { useTranslation } from "react-i18next";
import { libreChatSurface } from "./libreChatSurface";

export interface LibreChatShellProps {
  children: ReactNode;
  composer?: ReactNode;
  rightPanel?: ReactNode;
}

export function LibreChatShell({
  children,
  composer,
  rightPanel,
}: LibreChatShellProps) {
  const { t } = useTranslation();
  const [contextOpen, setContextOpen] = useState(false);
  const ContextIcon = contextOpen ? PanelRightClose : PanelRightOpen;

  return (
    <section
      className={libreChatSurface.root}
      data-librechat-shell="phase1"
      data-phase1-closure-shell
    >
      <div
        className={
          rightPanel && contextOpen
            ? libreChatSurface.workspaceWithContext
            : libreChatSurface.workspace
        }
      >
        <div className={libreChatSurface.thread}>
          {rightPanel && (
            <div className="hidden justify-end px-3 pt-2 sm:flex sm:px-4">
              <button
                type="button"
                data-librechat-context-toggle
                aria-expanded={contextOpen}
                aria-label={t("workbench.contextLabel")}
                title={t("workbench.contextLabel")}
                onClick={() => setContextOpen((open) => !open)}
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-sidebar-panel-muted)] hover:text-[var(--theme-text)]"
              >
                <ContextIcon size={18} aria-hidden="true" />
              </button>
            </div>
          )}
          <div
            data-workbench-region="thread"
            className={libreChatSurface.threadBody}
          >
            {children}
          </div>
          {composer && (
            <div
              data-workbench-region="composer"
              className={libreChatSurface.composer}
            >
              {composer}
            </div>
          )}
        </div>

        {rightPanel && contextOpen && (
          <div
            data-workbench-region="context"
            className={libreChatSurface.context}
          >
            {rightPanel}
          </div>
        )}
      </div>
    </section>
  );
}
