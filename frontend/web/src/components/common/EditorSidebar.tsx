import { useCallback } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { useSidebarPanel } from "../../hooks/useSidebarPanel";

const STORAGE_KEY = "editor-sidebar-width";
const CSS_VAR = "--editor-sidebar-width";
const DEFAULT_WIDTH = 30;

export interface EditorSidebarProps {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: React.ReactNode;
  icon?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  /** "default" (30%) | "wide" (larger min-width) */
  width?: "default" | "wide";
}

export function EditorSidebar({
  open,
  onClose,
  title,
  subtitle,
  icon,
  children,
  footer,
  width = "default",
}: EditorSidebarProps) {
  const {
    isMobile,
    animateIn,
    panelRef,
    indicatorRef,
    dragHandleRef,
    swipeElementRef,
    justResized,
    handleResizeStart,
  } = useSidebarPanel({
    open,
    onClose,
    widthStorageKey: STORAGE_KEY,
    widthCssVar: CSS_VAR,
    defaultWidthPct: DEFAULT_WIDTH,
    dataAttr: "data-editor-sidebar",
  });

  const handleOverlayClick = useCallback(() => {
    if (justResized.current) return;
    onClose();
  }, [onClose, justResized]);

  if (!open) return null;

  return createPortal(
    <>
      {/* Overlay */}
      <div
        className={`editor-sidebar-overlay ${
          animateIn ? "editor-sidebar-overlay--visible" : ""
        }`}
        onClick={handleOverlayClick}
      />

      {/* Panel */}
      <div
        ref={(el) => {
          (panelRef as React.MutableRefObject<HTMLDivElement | null>).current =
            el;
          (
            swipeElementRef as React.MutableRefObject<HTMLElement | null>
          ).current = el;
        }}
        className={`editor-sidebar ${
          isMobile ? "editor-sidebar--mobile" : "editor-sidebar--sidebar"
        } ${width === "wide" ? "editor-sidebar--wide" : ""} ${
          animateIn ? "editor-sidebar--animate-in" : ""
        }`}
        style={
          !isMobile
            ? { width: `var(${CSS_VAR}, ${DEFAULT_WIDTH}%)` }
            : undefined
        }
        onClick={(e) => e.stopPropagation()}
      >
        {/* Desktop resize handle */}
        {!isMobile && (
          <>
            <div
              ref={indicatorRef}
              className="hidden sm:block fixed top-0 bottom-0 z-[301] pointer-events-none"
              style={{
                display: "none",
                left: 0,
                width: "2px",
                backgroundColor: "var(--theme-primary)",
                opacity: 0.4,
              }}
            />
            <div
              className="hidden sm:block absolute left-0 top-0 bottom-0 -translate-x-1/2 z-10 cursor-col-resize pointer-events-auto group"
              onMouseDown={handleResizeStart}
            >
              <div className="absolute inset-y-0 left-1/2 -translate-x-1/2 w-1 rounded-full bg-transparent group-hover:bg-[var(--theme-primary)]/50 transition-colors duration-200" />
            </div>
          </>
        )}

        {/* Mobile drag handle */}
        {isMobile && (
          <div ref={dragHandleRef} className="editor-sidebar-drag-handle" />
        )}

        {/* Header */}
        <div className="editor-sidebar-header">
          <div className="editor-sidebar-header-left">
            {icon && <div className="editor-sidebar-header-icon">{icon}</div>}
            <div className="min-w-0">
              <div className="editor-sidebar-header-title">{title}</div>
              {subtitle && (
                <div className="editor-sidebar-header-subtitle">{subtitle}</div>
              )}
            </div>
          </div>
          <button onClick={onClose} className="editor-sidebar-close-btn">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="editor-sidebar-body">{children}</div>

        {/* Footer (outside scroll area) */}
        {footer && <div className="editor-sidebar-footer">{footer}</div>}
      </div>
    </>,
    document.body,
  );
}
