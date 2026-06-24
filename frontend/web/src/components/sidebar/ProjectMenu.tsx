/**
 * Project context menu component for project actions
 */

import { useRef, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Edit2, Trash2, MessageSquarePlus, X } from "lucide-react";
import type { Project } from "../../types";
import { useSwipeToClose } from "../../hooks/useSwipeToClose";

interface ProjectMenuProps {
  project: Project;
  isOpen: boolean;
  onClose: () => void;
  onRename: () => void;
  onDelete: () => void;
  onNewSessionInProject?: (projectId: string) => void;
  anchorEl: HTMLElement | null;
}

export function ProjectMenu({
  project: _project,
  isOpen,
  onClose,
  onRename,
  onDelete,
  onNewSessionInProject,
  anchorEl,
}: ProjectMenuProps) {
  // _project is available for future use (e.g., showing project info in menu)
  const { t } = useTranslation();
  const menuRef = useRef<HTMLDivElement>(null);

  // Reactive mobile detection
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.innerWidth < 640;
  });

  const swipeRef = useSwipeToClose({
    onClose,
    enabled: isOpen && isMobile,
  });

  // Update isMobile on resize
  useEffect(() => {
    const handleResize = () => {
      setIsMobile(window.innerWidth < 640);
    };

    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        menuRef.current &&
        !menuRef.current.contains(event.target as Node) &&
        !anchorEl?.contains(event.target as Node)
      ) {
        onClose();
      }
    };

    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isOpen, onClose, anchorEl]);

  // Close on escape key
  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    if (isOpen) {
      document.addEventListener("keydown", handleEscape);
    }

    return () => {
      document.removeEventListener("keydown", handleEscape);
    };
  }, [isOpen, onClose]);

  if (!isOpen || !anchorEl) return null;

  // Mobile: bottom sheet style
  if (isMobile) {
    return (
      <>
        {/* Backdrop */}
        <div
          className="fixed inset-0 z-40 bg-slate-950/35 sm:hidden"
          onClick={onClose}
        />
        {/* Bottom sheet */}
        <div
          ref={(el) => {
            menuRef.current = el;
            swipeRef.current = el;
          }}
          className="fixed bottom-0 left-0 right-0 z-50 max-h-[70vh] overflow-y-auto rounded-t-lg border-x border-t border-[var(--theme-border)] bg-[var(--theme-bg-card)] shadow-[0_8px_24px_rgba(18,38,63,0.12)] sm:hidden"
        >
          {/* Handle bar */}
          <div className="flex justify-center py-2">
            <div className="w-10 h-1 rounded-full bg-stone-300 dark:bg-stone-600" />
          </div>

          {/* Header */}
          <div className="flex items-center justify-between px-4 pb-1.5">
            <span className="text-[13px] font-medium text-[var(--theme-text)]">
              {t("sidebar.projectOptions")}
            </span>
            <button
              onClick={onClose}
              className="rounded-lg p-1 text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)]"
              aria-label={t("common.close")}
            >
              <X size={16} />
            </button>
          </div>

          {/* Menu items */}
          <div className="bg-[var(--theme-bg-sidebar)] px-2 pb-4 pt-1">
            {/* New Session */}
            {onNewSessionInProject && (
              <button
                onClick={() => {
                  onNewSessionInProject(_project.id);
                  onClose();
                }}
                className="w-full flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-[13px] text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)]"
              >
                <MessageSquarePlus size={16} />
                <span>{t("sidebar.newChat")}</span>
              </button>
            )}

            {/* Rename */}
            <button
              onClick={() => {
                onRename();
                onClose();
              }}
              className="w-full flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-[13px] text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)]"
            >
              <Edit2 size={16} />
              <span>{t("sidebar.rename")}</span>
            </button>

            {/* Divider */}
            <div className="my-1.5 h-px bg-[var(--theme-border)]" />

            {/* Delete */}
            <button
              onClick={() => {
                onDelete();
                onClose();
              }}
              className="w-full flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-[13px] text-red-600 transition-colors hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/25"
            >
              <Trash2 size={16} />
              <span>{t("common.delete")}</span>
            </button>
          </div>
        </div>
      </>
    );
  }

  // Desktop: dropdown menu
  // Calculate menu position
  const rect = anchorEl.getBoundingClientRect();
  const menuStyle: React.CSSProperties = {
    position: "fixed",
    top: rect.bottom + 4,
    right: window.innerWidth - rect.right,
    zIndex: 50,
  };

  return (
    <div
      ref={menuRef}
      style={menuStyle}
      className="w-48 overflow-hidden rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] py-1 shadow-[0_8px_24px_rgba(18,38,63,0.12)]"
    >
      {/* Rename option */}
      <button
        onClick={() => {
          onRename();
          onClose();
        }}
        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)]"
      >
        <Edit2 size={14} />
        <span>{t("sidebar.rename")}</span>
      </button>

      {/* New Session option */}
      {onNewSessionInProject && (
        <button
          onClick={() => {
            onNewSessionInProject(_project.id);
            onClose();
          }}
          className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)]"
        >
          <MessageSquarePlus size={14} />
          <span>{t("sidebar.newChat")}</span>
        </button>
      )}

      {/* Divider */}
      <div className="my-1 h-px bg-[var(--theme-border)]" />

      {/* Delete option */}
      <button
        onClick={() => {
          onDelete();
          onClose();
        }}
        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-600 transition-colors hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/25"
      >
        <Trash2 size={14} />
        <span>{t("common.delete")}</span>
      </button>
    </div>
  );
}
