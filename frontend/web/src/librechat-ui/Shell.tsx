import { Button, DrawerRoot, DrawerPortal, DrawerBackdrop, DrawerPopup, DrawerHeader, DrawerTitle, DrawerContent } from "@lobehub/ui/base-ui";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { useTranslation } from "react-i18next";
import { libreChatSurface } from "./surface";
import { CloseContextPanelContext } from "./ContextPanel";

export interface LibreChatShellProps {
  children: ReactNode;
  header?: ReactNode;
  composer?: ReactNode;
  rightPanel?: ReactNode;
}

/** Owns the chat-first workbench geometry while leaving data authority to ai-platform. */
export function LibreChatShell({
  children,
  header,
  composer,
  rightPanel,
}: LibreChatShellProps) {
  const { t } = useTranslation();
  const [contextOpen, setContextOpen] = useState(false);
  const [desktop, setDesktop] = useState(() => typeof window !== "undefined" && window.matchMedia("(min-width: 1280px)").matches);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const contextWidthRef = useRef(360);
  const dragRef = useRef<{ x: number; width: number } | null>(null);
  const resizeContext = (width: number, persist = false) => {
    const available = (workspaceRef.current?.clientWidth ?? 1000) - 480;
    const next = Math.round(Math.max(280, Math.min(520, available, width)));
    contextWidthRef.current = next;
    workspaceRef.current?.style.setProperty("--context-panel-width", `${next}px`);
    if (persist) {
      try { localStorage.setItem("ai-platform-context-width", String(next)); } catch { /* Geometry remains usable without storage. */ }
    }
  };
  const [contextWidth, setContextWidth] = useState(360);

  useEffect(() => {
    let width = 360;
    try { width = Number(localStorage.getItem("ai-platform-context-width")) || 360; } catch { /* Use the default width. */ }
    const update = () => { resizeContext(width); setContextWidth(contextWidthRef.current); };
    update();
    const onResize = () => { resizeContext(contextWidthRef.current); setContextWidth(contextWidthRef.current); };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
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
        ref={workspaceRef}
        className={
          rightPanel && contextOpen && desktop
            ? libreChatSurface.workspaceWithContext
            : libreChatSurface.workspace
        }
      >
        <div className={libreChatSurface.thread}>
          {(header || rightPanel) && (
            <div
              data-workbench-region="header"
              className={libreChatSurface.threadHeader}
            >
              {header && <div className="min-w-0 flex-1">{header}</div>}
              {rightPanel && <Button type="text"
                ref={triggerRef}
                htmlType="button"
                data-librechat-context-toggle
                aria-controls="librechat-context-panel"
                aria-expanded={contextOpen}
                aria-label={t("workbench.contextLabel")}
                title={t("workbench.contextLabel")}
                onClick={() => setContextOpen((open) => !open)}
                className="ml-auto shrink-0 rounded-lg text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-sidebar-panel-muted)] hover:text-[var(--theme-text)]"
                icon={<ContextIcon size={18} aria-hidden="true" />}
              />}
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
            className={`${libreChatSurface.context} relative`}
          >
            <div role="separator" tabIndex={0} aria-label="调整文件面板宽度" aria-orientation="vertical"
              aria-valuemin={280} aria-valuemax={520} aria-valuenow={contextWidth}
              className="absolute inset-y-0 -left-1 z-10 w-2 cursor-col-resize touch-none rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--theme-primary)] hover:bg-[var(--theme-border)]"
              onPointerDown={(event) => {
                if (event.button !== 0) return;
                event.preventDefault();
                event.currentTarget.setPointerCapture(event.pointerId);
                dragRef.current = { x: event.clientX, width: contextWidthRef.current };
              }}
              onPointerMove={(event) => {
                if (!dragRef.current) return;
                resizeContext(dragRef.current.width + dragRef.current.x - event.clientX);
                setContextWidth(contextWidthRef.current);
              }}
              onLostPointerCapture={() => { dragRef.current = null; resizeContext(contextWidthRef.current, true); }}
              onKeyDown={(event) => {
                if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                resizeContext(event.key === "Home" ? 280 : event.key === "End" ? 520 : contextWidthRef.current + (event.key === "ArrowLeft" ? 20 : -20), true);
                setContextWidth(contextWidthRef.current);
              }}
            />
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
                <CloseContextPanelContext.Provider value={() => setContextOpen(false)}>
                  {rightPanel}
                </CloseContextPanelContext.Provider>
              </DrawerContent>
            </DrawerPopup>
          </DrawerPortal>
        </DrawerRoot>
      )}
    </section>
  );
}
