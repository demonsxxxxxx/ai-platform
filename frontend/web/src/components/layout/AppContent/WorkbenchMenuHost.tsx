import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import {
  Bell,
  History,
  ListTree,
  MessageSquarePlus,
  Moon,
  Sun,
} from "lucide-react";

import { useTheme } from "../../../contexts/ThemeContext";
import { notificationPublicApi } from "../../../services/api/notificationPublic";
import { NotificationDialog } from "../../notification/NotificationDialog";
import type { TabType } from "./types";

interface WorkbenchMenuHostProps {
  activeTab: TabType;
  onNewSession: () => void;
  currentRunId?: string | null;
  onOpenRunPlayback?: () => void;
  onToggleOutline?: () => void;
  showOutlineButton?: boolean;
  allowNewSessionAction?: boolean;
  newSessionActionLabel?: string;
}

/** Portals the shared workbench menu without adding page layout. */
export function WorkbenchMenuHost({
  activeTab,
  onNewSession,
  currentRunId,
  onOpenRunPlayback,
  onToggleOutline,
  showOutlineButton,
  allowNewSessionAction = true,
  newSessionActionLabel,
}: WorkbenchMenuHostProps) {
  const { t } = useTranslation();
  const { theme, toggleTheme } = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);
  const [notifDialogOpen, setNotifDialogOpen] = useState(false);
  const [activeNotifCount, setActiveNotifCount] = useState(0);
  const [menuPosition, setMenuPosition] = useState({ top: 8, right: 8 });
  const menuRef = useRef<HTMLDivElement>(null);

  const refreshNotifCount = useCallback(() => {
    notificationPublicApi
      .getActive()
      .then((items) => setActiveNotifCount(items.length));
  }, []);

  useEffect(() => {
    refreshNotifCount();
  }, [refreshNotifCount]);

  useEffect(() => {
    const handleOpen = (event: Event) => {
      setMenuPosition(
        (event as CustomEvent<{ top: number; right: number }>).detail,
      );
      setMenuOpen(true);
    };
    window.addEventListener("workbench-menu-open", handleOpen);
    return () => window.removeEventListener("workbench-menu-open", handleOpen);
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const timer = window.setTimeout(
      () => document.addEventListener("click", handleClickOutside),
      0,
    );
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("click", handleClickOutside);
    };
  }, [menuOpen]);

  const showRunPlaybackButton =
    activeTab === "chat" && !!currentRunId && !!onOpenRunPlayback;

  return (
    <>
      {menuOpen
        ? createPortal(
            <div
              ref={menuRef}
              className="fixed z-[301] w-56 overflow-hidden rounded-lg border shadow-[0_8px_18px_rgba(18,38,63,0.08)] animate-scale-in"
              style={{
                top: menuPosition.top,
                right: menuPosition.right,
                backgroundColor: "var(--theme-bg-card)",
                borderColor: "var(--theme-border)",
              }}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="py-1">
                {showOutlineButton && onToggleOutline ? (
                  <button
                    className="flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-primary-light)] hover:text-[var(--theme-text)]"
                    onClick={() => {
                      onToggleOutline();
                      setMenuOpen(false);
                    }}
                    type="button"
                  >
                    <span className="flex w-5 shrink-0 items-center justify-center">
                      <ListTree size={16} />
                    </span>
                    <span className="truncate">{t("chat.outline")}</span>
                  </button>
                ) : null}
                {showRunPlaybackButton ? (
                  <button
                    className="flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-primary-light)] hover:text-[var(--theme-text)]"
                    onClick={() => {
                      onOpenRunPlayback?.();
                      setMenuOpen(false);
                    }}
                    type="button"
                  >
                    <span className="flex w-5 shrink-0 items-center justify-center">
                      <History size={16} />
                    </span>
                    <span className="truncate">{t("runPlayback.menuLabel")}</span>
                  </button>
                ) : null}
                {activeTab === "chat" && allowNewSessionAction ? (
                  <button
                    className="flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-primary-light)] hover:text-[var(--theme-text)]"
                    onClick={() => {
                      onNewSession();
                      setMenuOpen(false);
                    }}
                    type="button"
                  >
                    <span className="flex w-5 shrink-0 items-center justify-center">
                      <MessageSquarePlus size={16} />
                    </span>
                    <span className="truncate">
                      {newSessionActionLabel ?? t("sidebar.newChat")}
                    </span>
                  </button>
                ) : null}
                <button
                  className="flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-primary-light)] hover:text-[var(--theme-text)]"
                  onClick={() => {
                    setNotifDialogOpen(true);
                    setMenuOpen(false);
                  }}
                  type="button"
                >
                  <span className="flex w-5 shrink-0 items-center justify-center">
                    <Bell size={16} />
                  </span>
                  <span className="truncate">{t("nav.notifications")}</span>
                  {activeNotifCount > 0 ? (
                    <span className="ml-auto flex h-3.5 min-w-[14px] items-center justify-center rounded-full bg-red-500 px-1 text-[9px] font-bold leading-none text-white">
                      {activeNotifCount > 99 ? "99+" : activeNotifCount}
                    </span>
                  ) : null}
                </button>
                <button
                  className="flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-primary-light)] hover:text-[var(--theme-text)]"
                  onClick={() => {
                    toggleTheme();
                    setMenuOpen(false);
                  }}
                  type="button"
                >
                  <span className="flex w-5 shrink-0 items-center justify-center">
                    {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
                  </span>
                  <span className="truncate">
                    {t(
                      theme === "light"
                        ? "theme.switchToDark"
                        : "theme.switchToLight",
                    )}
                  </span>
                </button>
              </div>
            </div>,
            document.body,
          )
        : null}
      <NotificationDialog
        isOpen={notifDialogOpen}
        onClose={() => setNotifDialogOpen(false)}
        onDismissed={refreshNotifCount}
      />
    </>
  );
}
