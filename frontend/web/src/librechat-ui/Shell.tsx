import { Button, DrawerRoot, DrawerPortal, DrawerBackdrop, DrawerPopup, DrawerHeader, DrawerTitle, DrawerContent } from "@lobehub/ui/base-ui";
import { useEffect, useRef, useState, type ReactNode } from "react";
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
  const [desktop, setDesktop] = useState(() => typeof window !== "undefined" && window.matchMedia("(min-width: 1280px)").matches);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const ContextIcon = contextOpen ? PanelRightClose : PanelRightOpen;

  useEffect(() => {
    const media = window.matchMedia("(min-width: 1280px)");
    const update = () => setDesktop(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    if (!contextOpen || !desktop) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      setContextOpen(false);
      triggerRef.current?.focus();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [contextOpen, desktop]);

  return (
    <section
      className={libreChatSurface.root}
      data-librechat-shell="phase1"
      data-phase1-closure-shell
    >
      <div
        className={
          rightPanel && contextOpen && desktop
            ? libreChatSurface.workspaceWithContext
            : libreChatSurface.workspace
        }
      >
        <div className={libreChatSurface.thread}>
          {rightPanel && (
            <div className="flex shrink-0 justify-end px-3 pt-2 sm:px-4">
              <Button type="text"
                ref={triggerRef}
                htmlType="button"
                data-librechat-context-toggle
                aria-controls="librechat-context-panel"
                aria-expanded={contextOpen}
                aria-label={t("workbench.contextLabel")}
                title={t("workbench.contextLabel")}
                onClick={() => setContextOpen((open) => !open)}
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-sidebar-panel-muted)] hover:text-[var(--theme-text)]"
                icon={<ContextIcon size={18} aria-hidden="true" />}
              />
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

        {rightPanel && contextOpen && desktop && (
          <aside
            id="librechat-context-panel"
            aria-label={t("workbench.contextLabel")}
            data-workbench-region="context"
            className={libreChatSurface.context}
          >
            {rightPanel}
          </aside>
        )}
      </div>
      {rightPanel && !desktop && (
        <DrawerRoot open={contextOpen} onOpenChange={setContextOpen}>
          <DrawerPortal>
            <DrawerBackdrop data-librechat-context-backdrop />
            <DrawerPopup placement="right" width="min(22rem, calc(100vw - 2rem))" finalFocus={triggerRef}>
              <DrawerHeader>
                <DrawerTitle>{t("workbench.contextLabel")}</DrawerTitle>
                <Button type="text" htmlType="button" aria-label={t("common.close", "关闭")}
                  onClick={() => setContextOpen(false)} icon={<PanelRightClose size={18} aria-hidden="true" />} />
              </DrawerHeader>
              <DrawerContent id="librechat-context-panel" data-workbench-region="context" style={{ minHeight: 0, overflow: "hidden", flexDirection: "column" }}>
                {rightPanel}
              </DrawerContent>
            </DrawerPopup>
          </DrawerPortal>
        </DrawerRoot>
      )}
    </section>
  );
}
