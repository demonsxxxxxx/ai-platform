/**
 * Session sidebar component for displaying and managing chat history.
 */

import {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
  forwardRef,
  useImperativeHandle,
  type CSSProperties,
} from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { useTranslation } from "react-i18next";
import { sessionApi, type BackendSession } from "../../services/api";
import { useAuth } from "../../hooks/useAuth";
import { useSessionList } from "../../hooks/useSession";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { RecentChatsDialog } from "../sidebar/RecentChatsDialog";
import {
  mergeUnreadUpdate,
  type UnreadBySession,
} from "../sidebar/unreadCounts";
import { SearchDialog } from "./SearchDialog";
import { ShareDialog } from "../share/ShareDialog";
import {
  SessionListContent,
  SidebarRail,
} from "./SidebarParts";
import type { SessionActions } from "./SidebarParts";
import {
  getSafeWorkbenchNavPath,
  type WorkbenchNavItem,
} from "./SidebarParts/navigationState";
import { canAccessWorkbenchItem } from "../governance/workbenchAccessPolicy";
import { LIBRECHAT_SHELL_GEOMETRY } from "../../librechat-ui/surface";

interface SessionSidebarProps {
  currentSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
  onNewSession: () => void;
  refreshKey?: number;
  newSession?: BackendSession | null;
  mobileOpen?: boolean;
  onMobileOpen?: () => void;
  onMobileClose?: () => void;
  isCollapsed?: boolean;
  onToggleCollapsed?: (collapsed: boolean) => void;
}

export interface SessionSidebarHandle {
  updateSessionUnread: (
    sessionId: string,
    unreadCount: number,
  ) => void;
}

export const SessionSidebar = forwardRef<
  SessionSidebarHandle,
  SessionSidebarProps
>(function SessionSidebar(
  {
    currentSessionId,
    onSelectSession,
    onNewSession,
    newSession,
    mobileOpen = false,
    onMobileOpen,
    onMobileClose,
    isCollapsed: externalCollapsed,
    onToggleCollapsed,
  },
  ref,
) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [internalCollapsed, setInternalCollapsed] = useState(false);
  const [isChatsCollapsed, setIsChatsCollapsed] = useState(false);
  const [scrollEl, setScrollEl] = useState<HTMLDivElement | null>(null);
  const [unreadBySession, setUnreadBySession] = useState<UnreadBySession>(
    () => new Map(),
  );
  const [shareDialogSessionId, setShareDialogSessionId] = useState<
    string | null
  >(null);
  const [shareDialogSessionName, setShareDialogSessionName] = useState("");
  const [isRecentChatsOpen, setIsRecentChatsOpen] = useState(false);

  const [isMobile, setIsMobile] = useState(
    () => window.matchMedia("(max-width: 639px)").matches,
  );

  const navigate = useNavigate();

  const navigateWorkbenchItem = useCallback(
    (item: WorkbenchNavItem) => {
      const destination = canAccessWorkbenchItem(user, item)
        ? getSafeWorkbenchNavPath(item, user)
        : "/chat";
      navigate(destination);
    },
    [navigate, user],
  );

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 639px)");
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  const isCollapsed = externalCollapsed ?? internalCollapsed;
  const setIsCollapsed = onToggleCollapsed ?? setInternalCollapsed;
  const sidebarGeometryStyle = {
    "--sidebar-rail-width": `${LIBRECHAT_SHELL_GEOMETRY.railWidthPx}px`,
    "--sidebar-width": `${LIBRECHAT_SHELL_GEOMETRY.expandedMinWidthPx}px`,
  } as CSSProperties;

  // ─── Hooks ──────────────────────────────────────────────────────

  const sessionList = useSessionList(scrollEl);

  const handleSessionUnread = useCallback(
    (sid: string, count: number) => {
      setUnreadBySession((prev) =>
        mergeUnreadUpdate(prev, {
          sessionId: sid,
          unreadCount: count,
        }),
      );
      const session = sessionList.sessions.find((s) => s.id === sid);
      if (session) {
        sessionList.updateSession({ ...session, unread_count: count });
      }
    },
    [sessionList],
  );

  useImperativeHandle(
    ref,
    () => ({ updateSessionUnread: handleSessionUnread }),
    [handleSessionUnread],
  );

  const lastAppliedNewSessionKeyRef = useRef<string | null>(null);
  const desktopRecentChatsBtnRef = useRef<HTMLButtonElement>(null);
  const mobileRecentChatsBtnRef = useRef<HTMLButtonElement>(null);
  const [recentChatsAnchor, setRecentChatsAnchor] = useState<
    "desktop" | "mobile"
  >("desktop");

  const handleShareSession = useCallback(
    (sessionId: string) => {
      const s = sessionList.sessions.find((item) => item.id === sessionId);
      const title =
        s?.name ||
        ((s?.metadata as Record<string, unknown> | undefined)?.title as
          | string
          | undefined) ||
        "";
      setShareDialogSessionId(sessionId);
      setShareDialogSessionName(title || t("sidebar.newChat"));
    },
    [sessionList, t],
  );

  // ─── Delete confirmation ────────────────────────────────────────

  const [deleteConfirm, setDeleteConfirm] = useState<{
    isOpen: boolean;
    sessionId: string | null;
  }>({ isOpen: false, sessionId: null });

  const confirmDeleteSession = async () => {
    const sessionId = deleteConfirm.sessionId;
    if (!sessionId) return;
    try {
      await sessionApi.delete(sessionId);
      sessionList.removeSession(sessionId);
      if (currentSessionId === sessionId) onNewSession();
      toast.success(t("sidebar.sessionDeleted"));
    } catch (err) {
      console.error("Failed to delete session:", err);
      toast.error(t("sidebar.deleteFailed"));
    } finally {
      setDeleteConfirm({ isOpen: false, sessionId: null });
    }
  };

  // ─── Effects ────────────────────────────────────────────────────

  useEffect(() => {
    if (!currentSessionId) return;
    sessionList.softRefresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentSessionId]);

  useEffect(() => {
    if (newSession && newSession.id) {
      const sessionKey = [
        newSession.id,
        newSession.updated_at,
        newSession.name ?? "",
      ].join(":");
      if (lastAppliedNewSessionKeyRef.current === sessionKey) return;
      sessionList.prependSession(newSession);
      sessionList.updateSession(newSession);
      lastAppliedNewSessionKeyRef.current = sessionKey;
    }
  }, [newSession, sessionList]);

  // ─── Keyboard shortcuts ──────────────────────────────────────────

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const isMac = navigator.platform.toUpperCase().indexOf("MAC") >= 0;
      const modifier = isMac ? e.metaKey : e.ctrlKey;
      if (modifier && e.key === "k") {
        e.preventDefault();
        setIsSearchOpen(true);
      }
      if (modifier && e.key === "n") {
        e.preventDefault();
        onNewSession();
      }
      if (modifier && e.shiftKey && (e.key === "O" || e.key === "o")) {
        e.preventDefault();
        onNewSession();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onNewSession]);

  useEffect(() => {
    if (!mobileOpen) return undefined;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onMobileClose?.();
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [mobileOpen, onMobileClose]);

  // ─── Select session helper (mobile close) ───────────────────────

  const selectAndClose = useCallback(
    (sessionId: string) => {
      handleSessionUnread(sessionId, 0);
      onSelectSession(sessionId);
      onMobileClose?.();
    },
    [handleSessionUnread, onSelectSession, onMobileClose],
  );

  // ─── Aggregated action objects for SessionListContent ────────────

  const sessionActions: SessionActions = useMemo(
    () => ({
      onDeleteSession: (id) =>
        setDeleteConfirm({ isOpen: true, sessionId: id }),
      onShareSession: handleShareSession,
      onSelectSession: selectAndClose,
    }),
    [
      handleShareSession,
      selectAndClose,
    ],
  );

  // ─── JSX ────────────────────────────────────────────────────────

  return (
    <>
      <div
        className={`fixed inset-0 z-[60] bg-[var(--theme-overlay-strong)] sm:hidden transition-opacity duration-300 ease-in-out ${
          mobileOpen ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
        style={{ height: "var(--app-viewport-height, 100dvh)" }}
        onClick={onMobileClose}
      />

      <div
        data-librechat-mobile-sidebar
        className={`fixed left-0 top-0 z-[70] flex w-64 flex-col rounded-r-lg border-r border-[var(--theme-border)] bg-[var(--theme-sidebar-panel)] transition-transform duration-300 ease-in-out sm:hidden ${
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        }`}
        style={{
          ...sidebarGeometryStyle,
          width: LIBRECHAT_SHELL_GEOMETRY.mobileMaxWidth,
          height: "var(--app-viewport-height, 100dvh)",
          paddingTop: "env(safe-area-inset-top)",
          paddingBottom: "env(safe-area-inset-bottom)",
        }}
      >
        {isMobile ? (
          <SessionListContent
            user={user}
            onCollapse={() => {
              setIsCollapsed(true);
              onMobileClose?.();
            }}
            onNewSession={onNewSession}
            onOpenSearch={() => setIsSearchOpen(true)}
            onSetScrollEl={setScrollEl}
            sessions={sessionList.sessions}
            isLoading={sessionList.isLoading}
            hasMore={sessionList.hasMore}
            isLoadingMore={sessionList.isLoadingMore}
            loadMoreRef={sessionList.loadMoreRef}
            onUpdateSession={sessionList.updateSession}
            currentSessionId={currentSessionId}
            unreadBySession={unreadBySession}
            sessionActions={sessionActions}
            isChatsCollapsed={isChatsCollapsed}
            onToggleChatsCollapsed={() => setIsChatsCollapsed((v) => !v)}
          />
        ) : (
          <div className="flex-1" />
        )}
      </div>

      {/* Desktop: always render sidebar container */}
      <div
        data-librechat-desktop-sidebar
        className="hidden sm:flex h-full relative shrink-0 overflow-hidden bg-[var(--theme-sidebar-panel)]"
        style={{
          ...sidebarGeometryStyle,
          width: isCollapsed
            ? "var(--sidebar-rail-width)"
            : "var(--sidebar-width)",
        }}
      >
        {!isCollapsed ? (
          <div
            data-librechat-expanded-panel
            className="flex h-full w-full min-w-0 flex-col border-r border-[var(--theme-border)] bg-[var(--theme-sidebar-panel)]"
          >
            <SessionListContent
              user={user}
              onCollapse={() => setIsCollapsed(true)}
              onNewSession={onNewSession}
              onOpenSearch={() => setIsSearchOpen(true)}
              onSetScrollEl={setScrollEl}
              sessions={sessionList.sessions}
              isLoading={sessionList.isLoading}
              hasMore={sessionList.hasMore}
              isLoadingMore={sessionList.isLoadingMore}
              loadMoreRef={sessionList.loadMoreRef}
              onUpdateSession={sessionList.updateSession}
              currentSessionId={currentSessionId}
              unreadBySession={unreadBySession}
              sessionActions={sessionActions}
              isChatsCollapsed={isChatsCollapsed}
              onToggleChatsCollapsed={() => setIsChatsCollapsed((v) => !v)}
            />
          </div>
        ) : (
          <div className="absolute inset-0">
            <SidebarRail
              user={user}
              isExpanded={false}
              onExpand={() => setIsCollapsed(false)}
              onCollapse={() => setIsCollapsed(true)}
              onNewSession={() => {
                onNewSession();
                setIsRecentChatsOpen(false);
              }}
              onOpenSearch={() => {
                setIsSearchOpen(true);
                setIsRecentChatsOpen(false);
              }}
              onOpenRecentChats={() => {
                setRecentChatsAnchor("desktop");
                setIsRecentChatsOpen(true);
              }}
              onOpenLaunchpad={() => navigate("/apps")}
              onOpenSkills={() => navigate("/skills")}
              onOpenMcp={() => navigate("/mcp")}
              onOpenModels={() => navigateWorkbenchItem("models")}
              onOpenFiles={() => navigateWorkbenchItem("files")}
              recentChatsBtnRef={desktopRecentChatsBtnRef}
            />
          </div>
        )}
      </div>

      <div
        data-librechat-mobile-rail
        className="sm:hidden h-full relative shrink-0 overflow-hidden bg-[var(--theme-sidebar-rail)]"
        style={{
          ...sidebarGeometryStyle,
          width: "var(--sidebar-rail-width)",
        }}
      >
        <SidebarRail
          user={user}
          isExpanded={false}
          onExpand={() => onMobileOpen?.()}
          onCollapse={() => onMobileClose?.()}
          onNewSession={() => {
            onNewSession();
            setIsRecentChatsOpen(false);
          }}
          onOpenSearch={() => {
            setIsSearchOpen(true);
            setIsRecentChatsOpen(false);
          }}
          onOpenRecentChats={() => {
            setRecentChatsAnchor("mobile");
            setIsRecentChatsOpen(true);
          }}
          onOpenLaunchpad={() => navigate("/apps")}
          onOpenSkills={() => navigate("/skills")}
          onOpenMcp={() => navigate("/mcp")}
          onOpenModels={() => navigateWorkbenchItem("models")}
          onOpenFiles={() => navigateWorkbenchItem("files")}
          recentChatsBtnRef={mobileRecentChatsBtnRef}
        />
      </div>

      {isSearchOpen && (
        <SearchDialog
          isOpen={isSearchOpen}
          onClose={() => setIsSearchOpen(false)}
          onSelectSession={(sessionId) => {
            selectAndClose(sessionId);
            setIsSearchOpen(false);
          }}
        />
      )}

      <ConfirmDialog
        isOpen={deleteConfirm.isOpen}
        title={t("sidebar.deleteSession")}
        message={t("sidebar.deleteConfirm")}
        confirmText={t("common.delete")}
        cancelText={t("common.cancel")}
        onConfirm={confirmDeleteSession}
        onCancel={() => setDeleteConfirm({ isOpen: false, sessionId: null })}
        variant="danger"
      />

      <ShareDialog
        isOpen={shareDialogSessionId !== null}
        onClose={() => setShareDialogSessionId(null)}
        sessionId={shareDialogSessionId ?? ""}
        sessionName={shareDialogSessionName || t("sidebar.newChat")}
      />

      <RecentChatsDialog
        isOpen={isRecentChatsOpen}
        onClose={() => setIsRecentChatsOpen(false)}
        onSelectSession={(id) => selectAndClose(id)}
        currentSessionId={currentSessionId}
        anchorEl={
          recentChatsAnchor === "mobile"
            ? mobileRecentChatsBtnRef.current
            : desktopRecentChatsBtnRef.current
        }
      />
    </>
  );
});
