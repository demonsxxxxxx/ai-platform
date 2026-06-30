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
import { useProjectSessionList } from "../../hooks/useSession";
import { useProjectManager } from "../../hooks/useProjectManager";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { RecentChatsDialog } from "../sidebar/RecentChatsDialog";
import {
  mergeUnreadUpdate,
  type UnreadBySession,
} from "../sidebar/unreadCounts";
import { isSessionFavorite } from "../sidebar/sessionFavorites";
import { SearchDialog } from "./SearchDialog";
import { ShareDialog } from "../share/ShareDialog";
import {
  SessionListContent,
  SidebarRail,
} from "./SidebarParts";
import type { SessionActions } from "./SidebarParts";
import {
  getWorkbenchNavPath,
  type WorkbenchNavItem,
} from "./SidebarParts/navigationState";
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
  onShowProfile?: () => void;
}

export interface SessionSidebarHandle {
  updateSessionUnread: (
    sessionId: string,
    unreadCount: number,
    projectId?: string | null,
    isFavorite?: boolean,
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
    refreshKey,
    newSession,
    mobileOpen = false,
    onMobileOpen,
    onMobileClose,
    isCollapsed: externalCollapsed,
    onToggleCollapsed,
    onShowProfile,
  },
  ref,
) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [imgError, setImgError] = useState(false);
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
    (item: WorkbenchNavItem) => navigate(getWorkbenchNavPath(item)),
    [navigate],
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

  const uncategorizedList = useProjectSessionList("all", scrollEl);

  const handleSessionUnread = useCallback(
    (
      sid: string,
      count: number,
      projectId?: string | null,
      isFavorite?: boolean,
    ) => {
      setUnreadBySession((prev) =>
        mergeUnreadUpdate(prev, {
          sessionId: sid,
          unreadCount: count,
          projectId,
          isFavorite,
        }),
      );
      const session = uncategorizedList.sessions.find((s) => s.id === sid);
      if (session) {
        uncategorizedList.updateSession({ ...session, unread_count: count });
      }
    },
    [uncategorizedList],
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

  const projectManager = useProjectManager();
  const { projects } = projectManager;

  const handleMoveSession = useCallback(
    async (sessionId: string, projectId: string | null) => {
      try {
        const response = await sessionApi.moveToProject(sessionId, projectId);
        if (response.session) {
          const favorite = isSessionFavorite(response.session);
          uncategorizedList.updateSession(response.session);
          setUnreadBySession((prev) =>
            mergeUnreadUpdate(prev, {
              sessionId,
              unreadCount: response.session.unread_count ?? 0,
              projectId:
                (response.session.metadata?.project_id as
                  | string
                  | null
                  | undefined) ?? null,
              isFavorite: favorite,
            }),
          );
        }
      } catch (err) {
        console.error("Failed to move session:", err);
        toast.error(t("sidebar.sessionMoveFailed"));
      }
    },
    [uncategorizedList, t],
  );

  const handleShareSession = useCallback(
    (sessionId: string) => {
      const s = uncategorizedList.sessions.find((item) => item.id === sessionId);
      const title =
        s?.name ||
        ((s?.metadata as Record<string, unknown> | undefined)?.title as
          | string
          | undefined) ||
        "";
      setShareDialogSessionId(sessionId);
      setShareDialogSessionName(title || t("sidebar.newChat"));
    },
    [uncategorizedList, t],
  );

  const handleToggleFavorite = useCallback(
    async (sessionId: string) => {
      try {
        const response = await sessionApi.toggleFavorite(sessionId);
        const updatedSession = response.session;

        if (uncategorizedList.sessions.some((s) => s.id === sessionId)) {
          uncategorizedList.updateSession(updatedSession);
        }
        setUnreadBySession((prev) =>
          mergeUnreadUpdate(prev, {
            sessionId,
            unreadCount: updatedSession.unread_count ?? 0,
            projectId:
              (updatedSession.metadata?.project_id as
                | string
                | null
                | undefined) ?? null,
            isFavorite: response.is_favorite,
          }),
        );
      } catch (err) {
        console.error("Failed to toggle favorite:", err);
        toast.error(t("sidebar.favoriteToggleFailed", "收藏状态更新失败"));
      }
    },
    [t, uncategorizedList],
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
      uncategorizedList.removeSession(sessionId);
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
    projectManager.loadProjects();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  useEffect(() => {
    if (!currentSessionId) return;
    uncategorizedList.softRefresh();
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
      uncategorizedList.prependSession(newSession);
      uncategorizedList.updateSession(newSession);
      lastAppliedNewSessionKeyRef.current = sessionKey;
    }
  }, [newSession, uncategorizedList]);

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
      const uncategorizedSession = uncategorizedList.sessions.find(
        (session) => session.id === sessionId,
      );
      handleSessionUnread(
        sessionId,
        0,
        (uncategorizedSession?.metadata?.project_id as
          | string
          | null
          | undefined) ?? null,
        uncategorizedSession
          ? isSessionFavorite(uncategorizedSession)
          : undefined,
      );
      onSelectSession(sessionId);
      onMobileClose?.();
    },
    [uncategorizedList, handleSessionUnread, onSelectSession, onMobileClose],
  );

  // ─── Aggregated action objects for SessionListContent ────────────

  const sessionActions: SessionActions = useMemo(
    () => ({
      onDeleteSession: (id) =>
        setDeleteConfirm({ isOpen: true, sessionId: id }),
      onMoveSession: handleMoveSession,
      onToggleFavorite: handleToggleFavorite,
      onShareSession: handleShareSession,
      onSelectSession: selectAndClose,
    }),
    [
      handleMoveSession,
      handleToggleFavorite,
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
            imgError={imgError}
            onImgError={() => setImgError(true)}
            onCollapse={() => {
              setIsCollapsed(true);
              onMobileClose?.();
            }}
            onNewSession={onNewSession}
            onOpenSearch={() => setIsSearchOpen(true)}
            onShowProfile={onShowProfile!}
            onSetScrollEl={setScrollEl}
            uncategorizedSessions={uncategorizedList.sessions}
            isUncategorizedLoading={uncategorizedList.isLoading}
            hasMoreUncategorized={uncategorizedList.hasMore}
            isLoadingMoreUncategorized={uncategorizedList.isLoadingMore}
            loadMoreRef={uncategorizedList.loadMoreRef}
            onUpdateUncategorizedSession={uncategorizedList.updateSession}
            projects={projects}
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
              imgError={imgError}
              onImgError={() => setImgError(true)}
              onCollapse={() => setIsCollapsed(true)}
              onNewSession={onNewSession}
              onOpenSearch={() => setIsSearchOpen(true)}
              onShowProfile={onShowProfile!}
              onSetScrollEl={setScrollEl}
              uncategorizedSessions={uncategorizedList.sessions}
              isUncategorizedLoading={uncategorizedList.isLoading}
              hasMoreUncategorized={uncategorizedList.hasMore}
              isLoadingMoreUncategorized={uncategorizedList.isLoadingMore}
              loadMoreRef={uncategorizedList.loadMoreRef}
              onUpdateUncategorizedSession={uncategorizedList.updateSession}
              projects={projects}
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
              imgError={imgError}
              onImgError={() => setImgError(true)}
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
              onOpenMarketplace={() => navigate("/marketplace")}
              onOpenMcp={() => navigate("/mcp")}
              onOpenChannels={() => navigateWorkbenchItem("channels")}
              onOpenAgents={() => navigateWorkbenchItem("agents")}
              onOpenModels={() => navigateWorkbenchItem("models")}
              onOpenPersona={() => navigateWorkbenchItem("persona")}
              onOpenFiles={() => navigateWorkbenchItem("files")}
              onOpenRoles={() => navigateWorkbenchItem("roles")}
              recentChatsBtnRef={desktopRecentChatsBtnRef}
              onShowProfile={onShowProfile!}
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
          imgError={imgError}
          onImgError={() => setImgError(true)}
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
          onOpenMarketplace={() => navigate("/marketplace")}
          onOpenMcp={() => navigate("/mcp")}
          onOpenChannels={() => navigateWorkbenchItem("channels")}
          onOpenAgents={() => navigateWorkbenchItem("agents")}
          onOpenModels={() => navigateWorkbenchItem("models")}
          onOpenPersona={() => navigateWorkbenchItem("persona")}
          onOpenFiles={() => navigateWorkbenchItem("files")}
          onOpenRoles={() => navigateWorkbenchItem("roles")}
          recentChatsBtnRef={mobileRecentChatsBtnRef}
          onShowProfile={onShowProfile!}
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
