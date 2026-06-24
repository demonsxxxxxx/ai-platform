import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { MessageSquareText } from "lucide-react";
import { sessionApi } from "../../services/api/session";
import { getSessionTitle } from "../panels/sessionHelpers";
import type { BackendSession } from "../../services/api/session";
import { formatDateTime } from "../../utils/datetime";

interface RecentChatsDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onSelectSession: (sessionId: string) => void;
  currentSessionId?: string | null;
  anchorEl: HTMLElement | null;
}

export function RecentChatsDialog({
  isOpen,
  onClose,
  onSelectSession,
  currentSessionId,
  anchorEl,
}: RecentChatsDialogProps) {
  const { t } = useTranslation();
  const [sessions, setSessions] = useState<BackendSession[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [position, setPosition] = useState<{ top: number; left: number }>({
    top: 0,
    left: 0,
  });

  const loadSessions = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await sessionApi.list({ limit: 20 });
      const list = Array.isArray(response) ? response : response.sessions;
      setSessions(list);
    } catch (error) {
      console.error("Failed to load recent sessions:", error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) loadSessions();
  }, [isOpen, loadSessions]);

  useEffect(() => {
    if (!isOpen || !anchorEl) return;
    const rect = anchorEl.getBoundingClientRect();
    const panelWidth = 280;
    const panelMaxHeight = 480;
    let top = rect.bottom + 2;
    let left = rect.right + 2;

    if (left + panelWidth > window.innerWidth) {
      left = rect.left - panelWidth - 2;
    }
    if (top + panelMaxHeight > window.innerHeight) {
      top = window.innerHeight - panelMaxHeight - 8;
    }

    setPosition({ top, left });
  }, [isOpen, anchorEl]);

  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        anchorEl?.contains(target) ||
        document.getElementById("recent-chats-popover")?.contains(target)
      ) {
        return;
      }
      onClose();
    };
    const timer = setTimeout(() => {
      document.addEventListener("click", handler);
    }, 0);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("click", handler);
    };
  }, [isOpen, onClose, anchorEl]);

  useEffect(() => {
    if (isOpen) {
      const handler = (e: KeyboardEvent) => {
        if (e.key === "Escape") onClose();
      };
      document.addEventListener("keydown", handler);
      return () => document.removeEventListener("keydown", handler);
    }
  }, [isOpen, onClose]);

  if (!isOpen || !anchorEl) return null;

  return createPortal(
    <div
      id="recent-chats-popover"
      className="fixed z-[301] w-[280px] overflow-hidden rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] shadow-[0_8px_24px_rgba(18,38,63,0.12)] animate-scale-in"
      style={{
        top: position.top,
        left: position.left,
        maxHeight: "480px",
      }}
    >
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-4 pt-3 pb-2 shrink-0">
        <span className="flex size-6 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] text-[var(--theme-primary)] ring-1 ring-[var(--theme-border)]">
          <MessageSquareText size={14} />
        </span>
        <span className="text-sm font-semibold text-[var(--theme-text)] leading-none">
          {t("sidebar.recentChats")}
        </span>
      </div>

      {/* Session list */}
      <div className="max-h-[420px] overflow-y-auto bg-[var(--theme-bg-sidebar)] py-1">
        {isLoading ? (
          <div className="px-4 py-2 space-y-1">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3 px-0 py-2.5">
                <div className="flex-1 min-w-0 space-y-1.5">
                  <div
                    className="skeleton-line h-[13px] rounded-md"
                    style={{ width: i % 2 === 0 ? "75%" : "60%" }}
                  />
                  <div
                    className="skeleton-line h-[11px] rounded-md !opacity-50"
                    style={{ width: "40%" }}
                  />
                </div>
              </div>
            ))}
          </div>
        ) : sessions.length === 0 ? (
          <div className="py-10 text-center text-xs text-[var(--theme-text-tertiary)]">
            {t("sidebar.noSessions") || "No recent chats"}
          </div>
        ) : (
          sessions.map((session) => (
            <button
              key={session.id}
              onClick={() => {
                onSelectSession(session.id);
                onClose();
              }}
              className={`w-full flex items-center gap-3 px-4 py-2.5 transition-colors text-left group ${
                session.id === currentSessionId
                  ? "bg-[var(--theme-primary-light)]"
                  : "hover:bg-[var(--theme-hover)]"
              }`}
            >
              <div className="min-w-0 flex-1">
                <div
                  className={`truncate text-[13px] ${
                    session.id === currentSessionId
                      ? "font-medium text-[var(--theme-text)]"
                      : "text-[var(--theme-text-secondary)] group-hover:text-[var(--theme-text)]"
                  }`}
                >
                  {getSessionTitle(session, t)}
                </div>
                <div className="mt-0.5 text-[11px] text-[var(--theme-text-tertiary)]">
                  {formatDateTime(session.updated_at)}
                </div>
              </div>
              {session.unread_count != null && session.unread_count > 0 && (
                <span className="shrink-0 inline-flex h-4 min-w-[16px] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-medium leading-none text-white">
                  {session.unread_count}
                </span>
              )}
            </button>
          ))
        )}
      </div>
    </div>,
    document.body,
  );
}
