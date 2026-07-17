/**
 * Session context menu component for session actions
 */

import { useRef, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Edit2,
  Trash2,
  X,
} from "lucide-react";
import type { BackendSession } from "../../services/api/session";
import { useSwipeToClose } from "../../hooks/useSwipeToClose";

interface SessionMenuProps {
  session: BackendSession;
  isOpen: boolean;
  onClose: () => void;
  onRename: () => void;
  onDelete: () => void;
  anchorEl: HTMLElement | null;
}

export function SessionMenu({
  session: _session,
  isOpen,
  onClose,
  onRename,
  onDelete,
  anchorEl,
}: SessionMenuProps) {
  const { t } = useTranslation();
  const menuRef = useRef<HTMLDivElement>(null);

  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.innerWidth < 640;
  });

  const swipeRef = useSwipeToClose({
    onClose,
    enabled: isOpen && isMobile,
  });

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 640);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  // Close menu when clicking outside
  useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (
        menuRef.current &&
        !menuRef.current.contains(event.target as Node) &&
        !anchorEl?.contains(event.target as Node)
      ) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen, onClose, anchorEl]);

  // Close on escape key
  useEffect(() => {
    if (!isOpen) return;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [isOpen, onClose]);

  if (!isOpen || !anchorEl) return null;

  // ── Main menu items ──────────────────────────────────────────────
  const mainMenu = (
    <>
      {/* Rename */}
      <button
        onClick={() => {
          onRename();
          onClose();
        }}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-sm text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-primary-light)] transition-colors"
      >
        <Edit2 size={16} className="shrink-0" />
        <span>{t("sidebar.rename")}</span>
      </button>

      {/* Divider */}
      <div
        className="h-px my-1 mx-2"
        style={{ background: "var(--theme-border)" }}
      />

      {/* Delete */}
      <button
        onClick={() => {
          onDelete();
          onClose();
        }}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-sm text-red-500 hover:bg-red-500/10 transition-colors"
      >
        <Trash2 size={16} className="shrink-0" />
        <span>{t("common.delete")}</span>
      </button>
    </>
  );

  // ── Mobile: bottom sheet ──────────────────────────────────────────
  if (isMobile) {
    return (
      <>
        <div
          className="fixed inset-0 z-40 bg-[var(--theme-overlay-strong)] sm:hidden"
          onClick={onClose}
        />
        <div
          ref={(el) => {
            menuRef.current = el;
            swipeRef.current = el;
          }}
          className="fixed bottom-0 left-0 right-0 z-50 sm:hidden rounded-t-lg shadow-[0_8px_24px_rgba(18,38,63,0.12)] max-h-[70vh] overflow-y-auto animate-in fade-in slide-in-from-bottom-4 duration-200"
          style={{ backgroundColor: "var(--theme-bg-card)" }}
        >
          <div className="flex justify-center py-2">
            <div
              className="w-10 h-1 rounded-full"
              style={{ background: "var(--theme-border)" }}
            />
          </div>

          <div
            className="flex items-center justify-between px-4 pb-2"
            style={{ color: "var(--theme-text)" }}
          >
            <span className="text-sm font-medium">
              {t("sidebar.sessionOptions")}
            </span>
            <button
              onClick={onClose}
              className="p-1 rounded-full transition-colors"
              style={{ color: "var(--theme-text-secondary)" }}
            >
              <X size={18} />
            </button>
          </div>

          <div className="px-2 pb-4">
            {mainMenu}
          </div>
        </div>
      </>
    );
  }

  // ── Desktop: dropdown ─────────────────────────────────────────────
  const rect = anchorEl.getBoundingClientRect();
  const spaceBelow = window.innerHeight - rect.bottom;
  const spaceAbove = rect.top;
  const openBelow = spaceBelow >= spaceAbove;

  const menuStyle: React.CSSProperties = {
    position: "fixed",
    ...(openBelow
      ? { top: rect.bottom + 4 }
      : { bottom: window.innerHeight - rect.top + 4 }),
    right: window.innerWidth - rect.right,
    maxHeight: (openBelow ? spaceBelow : spaceAbove) - 16,
    overflowY: "auto",
    zIndex: 50,
  };

  return (
    <div
      ref={menuRef}
      style={{
        ...menuStyle,
        backgroundColor: "var(--theme-bg-card)",
        borderColor: "var(--theme-border)",
      }}
      className="w-56 rounded-lg border shadow-[0_8px_24px_rgba(18,38,63,0.12)] overflow-hidden animate-in fade-in zoom-in-95 duration-150 origin-top-right"
    >
      {mainMenu}
    </div>
  );
}
