/**
 * Session sidebar component for displaying and managing chat history.
 * Phase 1 shows the current session list only; project/favorite filtered lists
 * need backend query support before they can be re-enabled.
 */

import {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
  forwardRef,
  useImperativeHandle,
} from "react";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Users,
  Shield,
  Bot,
  Cpu,
  Star,
  Bell,
  Settings,
  Server,
  Brain,
  MessageCircle,
  Sparkles,
} from "lucide-react";
import type { BackendSession } from "../../services/api";
import { useAuth } from "../../hooks/useAuth";
import { useSettingsContext } from "../../contexts/SettingsContext";
import { canShowSurfaceInNavigation } from "../layout/AppContent/phase1SurfacePolicy";
import { useSessionList } from "../../hooks/useSession";
import { useSwipeToClose } from "../../hooks/useSwipeToClose";
import { RecentChatsDialog } from "../sidebar/RecentChatsDialog";
import {
  mergeUnreadUpdate,
  type UnreadBySession,
} from "../sidebar/unreadCounts";
import { SearchDialog } from "./SearchDialog";
import {
  SessionListContent,
  SidebarRail,
  MobileMoreMenuSheet,
  DesktopMoreMenu,
} from "./SidebarParts";
import type { SessionActions } from "./SidebarParts";

interface SessionSidebarProps {
  currentSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
  onNewSession: () => void;
  refreshKey?: number;
  newSession?: BackendSession | null;
  mobileOpen?: boolean;
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
    onMobileClose,
    isCollapsed: externalCollapsed,
    onToggleCollapsed,
    onShowProfile,
  },
  ref,
) {
  const { t } = useTranslation();
  const { user, permissions } = useAuth();
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [imgError, setImgError] = useState(false);
  const [internalCollapsed, setInternalCollapsed] = useState(true);
  const [isChatsCollapsed, setIsChatsCollapsed] = useState(false);
  const [scrollEl, setScrollEl] = useState<HTMLDivElement | null>(null);
  const [unreadBySession, setUnreadBySession] = useState<UnreadBySession>(
    () => new Map(),
  );
  const [isRecentChatsOpen, setIsRecentChatsOpen] = useState(false);
  const [isMoreMenuOpen, setIsMoreMenuOpen] = useState(false);
  const [moreMenuPosition, setMoreMenuPosition] = useState({ top: 0, left: 0 });
  const { enableMemory, enableSkills } = useSettingsContext();
  const canShow = (
    tab: Parameters<typeof canShowSurfaceInNavigation>[0],
    featureEnabled = true,
  ) => canShowSurfaceInNavigation(tab, permissions, featureEnabled);

  const moreMenuFeatureItems = [
    {
      path: "/skills",
      label: t("nav.skills"),
      icon: Sparkles,
      show: canShow("skills", enableSkills),
    },
    {
      path: "/mcp",
      label: t("nav.mcp"),
      icon: Server,
      show: canShow("mcp"),
    },
    {
      path: "/channels",
      label: t("nav.channels"),
      icon: MessageCircle,
      show: canShow("channels"),
    },
    {
      path: "/memory",
      label: t("nav.memory"),
      icon: Brain,
      show: canShow("memory", enableMemory),
    },
  ];

  const moreMenuUserItems = [
    {
      path: "/users",
      label: t("nav.users"),
      icon: Users,
      show: canShow("users"),
    },
    {
      path: "/roles",
      label: t("nav.roles"),
      icon: Shield,
      show: canShow("roles"),
    },
    {
      path: "/agents",
      label: t("nav.agents"),
      icon: Bot,
      show: canShow("agents"),
    },
    {
      path: "/models",
      label: t("nav.models"),
      icon: Cpu,
      show: canShow("models"),
    },
  ];

  const moreMenuSysItems = [
    {
      path: "/feedback",
      label: t("nav.feedback"),
      icon: Star,
      show: canShow("feedback"),
    },
    {
      path: "/notifications",
      label: t("nav.notifications"),
      icon: Bell,
      show: canShow("notifications"),
    },
    {
      path: "/settings",
      label: t("nav.systemSettings"),
      icon: Settings,
      show: canShow("settings"),
    },
  ];

  const hasMoreMenuItems =
    moreMenuFeatureItems.some((i) => i.show) ||
    moreMenuUserItems.some((i) => i.show) ||
    moreMenuSysItems.some((i) => i.show);

  const [isMobile, setIsMobile] = useState(
    () => window.matchMedia("(max-width: 639px)").matches,
  );

  const moreMenuRef = useRef<HTMLDivElement>(null);
  const moreMenuBtnRef = useRef<HTMLButtonElement>(null);
  const expandedMoreMenuBtnRef = useRef<HTMLButtonElement>(null);
  const moreMenuDragHandleRef = useRef<HTMLDivElement>(null);
  const moreMenuSwipeRef = useSwipeToClose({
    onClose: () => setIsMoreMenuOpen(false),
    enabled: isMoreMenuOpen && isMobile,
    dragHandleRef: moreMenuDragHandleRef,
  });
  const location = useLocation();

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 639px)");
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  useEffect(() => {
    if (!isMoreMenuOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (activeMoreMenuBtnRef.current?.contains(e.target as Node)) return;
      if (moreMenuRef.current?.contains(e.target as Node)) return;
      setIsMoreMenuOpen(false);
    };
    // Defer by one frame so the opening click event has finished bubbling
    // and the menu DOM is mounted (important on mobile where the same
    // click can re-trigger the listener before the menu renders).
    const id = requestAnimationFrame(() => {
      document.addEventListener("click", handleClickOutside);
    });
    return () => {
      cancelAnimationFrame(id);
      document.removeEventListener("click", handleClickOutside);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMoreMenuOpen]);

  useEffect(() => {
    if (isMoreMenuOpen) setIsMoreMenuOpen(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);

  const isCollapsed = externalCollapsed ?? internalCollapsed;
  const setIsCollapsed = onToggleCollapsed ?? setInternalCollapsed;
  const activeMoreMenuBtnRef = isCollapsed
    ? moreMenuBtnRef
    : expandedMoreMenuBtnRef;

  useEffect(() => {
    if (!isMoreMenuOpen || !activeMoreMenuBtnRef.current) return;
    const rect = activeMoreMenuBtnRef.current.getBoundingClientRect();
    const panelWidth = 208;
    const panelMaxHeight = 480;
    let top = rect.top;
    let left = rect.right + 2;
    if (left + panelWidth > window.innerWidth)
      left = window.innerWidth - panelWidth - 8;
    if (left < 8) left = 8;
    if (top + panelMaxHeight > window.innerHeight)
      top = window.innerHeight - panelMaxHeight - 8;
    if (top < 8) top = 8;
    setMoreMenuPosition({ top, left });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMoreMenuOpen, isCollapsed]);

  // ─── Hooks ──────────────────────────────────────────────────────

  const uncategorizedList = useSessionList(scrollEl);

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
  const recentChatsBtnRef = useRef<HTMLButtonElement>(null);

  // ─── Effects ────────────────────────────────────────────────────

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

  useEffect(() => {
    if (!refreshKey) return;
    uncategorizedList.softRefresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

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
        undefined,
      );
      onSelectSession(sessionId);
      onMobileClose?.();
    },
    [uncategorizedList, handleSessionUnread, onSelectSession, onMobileClose],
  );

  // ─── Aggregated action objects for SessionListContent ────────────

  const sessionActions: SessionActions = useMemo(
    () => ({
      onSelectSession: selectAndClose,
    }),
    [selectAndClose],
  );

  // ─── JSX ────────────────────────────────────────────────────────

  return (
    <>
      <div
        className={`fixed inset-0 z-[60] bg-black/40 sm:hidden transition-opacity duration-300 ease-in-out ${
          mobileOpen ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
        style={{ height: "var(--app-viewport-height, 100dvh)" }}
        onClick={onMobileClose}
      />

      <div
        className={`rounded-r-lg fixed left-0 top-0 z-[70] w-64 flex flex-col sm:hidden bg-[var(--theme-bg-sidebar)] transition-transform duration-300 ease-in-out ${
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        }`}
        style={{
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
            hasMoreMenuItems={hasMoreMenuItems}
            onToggleMoreMenu={() => setIsMoreMenuOpen((prev) => !prev)}
            expandedMoreMenuBtnRef={expandedMoreMenuBtnRef}
            onSetScrollEl={setScrollEl}
            uncategorizedSessions={uncategorizedList.sessions}
            isUncategorizedLoading={uncategorizedList.isLoading}
            hasMoreUncategorized={uncategorizedList.hasMore}
            isLoadingMoreUncategorized={uncategorizedList.isLoadingMore}
            loadMoreRef={uncategorizedList.loadMoreRef}
            onSoftRefreshUncategorized={uncategorizedList.softRefresh}
            currentSessionId={currentSessionId}
            unreadBySession={unreadBySession}
            sessionActions={sessionActions}
            isChatsCollapsed={isChatsCollapsed}
            onToggleChatsCollapsed={() => setIsChatsCollapsed((v) => !v)}
          />
        ) : (
          <div className="flex-1" />
        )}

        <MobileMoreMenuSheet
          featureItems={moreMenuFeatureItems}
          userItems={moreMenuUserItems}
          sysItems={moreMenuSysItems}
          isOpen={isMoreMenuOpen}
          onClose={() => setIsMoreMenuOpen(false)}
          menuRef={moreMenuRef}
          swipeRef={moreMenuSwipeRef}
          dragHandleRef={moreMenuDragHandleRef}
        />
      </div>

      {/* Desktop: always render sidebar container */}
      <div
        className="hidden sm:flex h-full relative shrink-0 overflow-hidden"
        style={{
          width: isCollapsed
            ? "var(--sidebar-rail-width)"
            : "var(--sidebar-width)",
        }}
      >
        <div
          className={`h-full w-full flex flex-col bg-[var(--theme-bg-sidebar)] border-r border-stone-200/60 dark:border-stone-800/60 ${
            isCollapsed ? "hidden" : ""
          }`}
        >
          {!isMobile ? (
            <SessionListContent
              user={user}
              imgError={imgError}
              onImgError={() => setImgError(true)}
              onCollapse={() => setIsCollapsed(true)}
              onNewSession={onNewSession}
              onOpenSearch={() => setIsSearchOpen(true)}
              onShowProfile={onShowProfile!}
              hasMoreMenuItems={hasMoreMenuItems}
              onToggleMoreMenu={() => setIsMoreMenuOpen((prev) => !prev)}
              expandedMoreMenuBtnRef={expandedMoreMenuBtnRef}
              onSetScrollEl={setScrollEl}
              uncategorizedSessions={uncategorizedList.sessions}
              isUncategorizedLoading={uncategorizedList.isLoading}
              hasMoreUncategorized={uncategorizedList.hasMore}
              isLoadingMoreUncategorized={uncategorizedList.isLoadingMore}
              loadMoreRef={uncategorizedList.loadMoreRef}
              onSoftRefreshUncategorized={uncategorizedList.softRefresh}
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

        <div
          className={`absolute inset-0 ${
            isCollapsed
              ? "opacity-100 pointer-events-auto"
              : "pointer-events-none opacity-0"
          }`}
        >
          <SidebarRail
            user={user}
            imgError={imgError}
            onImgError={() => setImgError(true)}
            onExpand={() => setIsCollapsed(false)}
            onNewSession={() => {
              onNewSession();
              setIsRecentChatsOpen(false);
            }}
            onOpenSearch={() => {
              setIsSearchOpen(true);
              setIsRecentChatsOpen(false);
            }}
            onOpenRecentChats={() => setIsRecentChatsOpen(true)}
            hasMoreMenuItems={hasMoreMenuItems}
            onToggleMoreMenu={() => {
              setIsMoreMenuOpen((prev) => !prev);
              setIsRecentChatsOpen(false);
            }}
            moreMenuBtnRef={moreMenuBtnRef}
            recentChatsBtnRef={recentChatsBtnRef}
            onShowProfile={onShowProfile!}
          />
        </div>
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

      <RecentChatsDialog
        isOpen={isRecentChatsOpen}
        onClose={() => setIsRecentChatsOpen(false)}
        onSelectSession={(id) => selectAndClose(id)}
        currentSessionId={currentSessionId}
        anchorEl={recentChatsBtnRef.current}
      />

      {!isMobile && (
        <DesktopMoreMenu
          featureItems={moreMenuFeatureItems}
          userItems={moreMenuUserItems}
          sysItems={moreMenuSysItems}
          isOpen={isMoreMenuOpen}
          onClose={() => setIsMoreMenuOpen(false)}
          menuRef={moreMenuRef}
          position={moreMenuPosition}
        />
      )}
    </>
  );
});
