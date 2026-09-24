import { useEffect, useState, type ReactNode } from "react";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { useTranslation } from "react-i18next";
import { libreChatSurface } from "./surface";

export interface LibreChatShellProps {
  children: ReactNode;
  composer?: ReactNode;
  rightPanel?: ReactNode;
}

/** Owns the chat-first workbench geometry while leaving data authority to ai-platform. */
export function LibreChatShell({
  children,
  composer,
  rightPanel,
}: LibreChatShellProps) {
  const { t } = useTranslation();
  const [contextOpen, setContextOpen] = useState(false);
  const ContextIcon = contextOpen ? PanelRightClose : PanelRightOpen;

  useEffect(() => {
    if (!contextOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setContextOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [contextOpen]);

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
            <div className="absolute right-3 top-2 z-[60] flex sm:right-4">
              <button
                type="button"
                data-librechat-context-toggle
                aria-controls="librechat-context-panel"
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
          <>
            <button
              type="button"
              aria-label={t("common.close", "关闭")}
              title={t("common.close", "关闭")}
              className="fixed inset-0 z-40 bg-black/20 xl:hidden"
              onClick={() => setContextOpen(false)}
            />
            <div
              id="librechat-context-panel"
              data-workbench-region="context"
              className={libreChatSurface.context}
            >
              {rightPanel}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
