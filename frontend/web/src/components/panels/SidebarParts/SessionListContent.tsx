import { Skeleton, Button } from "@lobehub/ui/base-ui";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ChevronDown,
  Search,
  MessageSquarePlus,
  MessageSquare,
  LayoutGrid,
  Package,
  Plug,
  Server,
  Bot,
  Cpu,
  Activity,
  Settings,
  PanelLeftClose,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { AgentIdentityAvatar } from "../../agent/AgentIdentityAvatar";
import { UserMenu } from "../../layout/UserMenu";
import { Spin } from "@lobehub/ui/base-ui";
import type { BackendSession } from "../../../services/api";
import {
  formatUnreadCount,
  getUnreadCount,
  type UnreadBySession,
} from "../../sidebar/unreadCounts";
import { groupSessionsByAgent, groupSessionsByTime } from "../sessionHelpers";
import { SessionItem } from "../../sidebar/SessionItem";
import { ThreadList } from "./ThreadList";
import { APP_NAME } from "../../../constants";
import {
  getWorkbenchNavItemFromPathname,
  type WorkbenchNavItem,
} from "./navigationState";
import { LibreChatPanelSection } from "../../../librechat-ui/Panel";
import {
  canAccessWorkbenchItem,
} from "../../governance/workbenchAccessPolicy";
import type { AgentProfileAvatarRef } from "../../../types/agentProfile";
import { isAiAdminUser } from "../capabilityAdmin";

export interface SessionActions {
  onDeleteSession: (id: string) => void;
  onSelectSession: (id: string) => void;
}

interface SessionListContentProps {
  user: {
    username?: string;
    avatar_url?: string;
    roles?: string[];
    is_admin?: boolean;
  } | null;
  onCollapse: () => void;
  onNewSession: () => void;
  onOpenSearch: () => void;
  onSetScrollEl: (el: HTMLDivElement | null) => void;
  sessions: BackendSession[];
  globalSessions: BackendSession[];
  isLoading: boolean;
  globalIsLoading: boolean;
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMoreRef: React.RefCallback<HTMLElement>;
  onUpdateSession: (s: BackendSession) => void;
  onRenameSession?: (
    sessionId: string,
    title: string,
  ) => Promise<BackendSession | void>;
  onRenameGlobalSession?: (
    sessionId: string,
    title: string,
  ) => Promise<BackendSession | void>;
  onSelectGlobalSession: (session: BackendSession) => void;
  onUpdateGlobalSession: (session: BackendSession) => void;
  currentSessionId: string | null;
  unreadBySession: UnreadBySession;
  sessionActions: SessionActions;
  isChatsCollapsed: boolean;
  onToggleChatsCollapsed: () => void;
  agentWorkspace?: {
    agent_id?: string;
    avatar_ref?: AgentProfileAvatarRef;
    avatar_seed?: string;
    name: string;
    description: string;
  };
  agentHistoryInMainPanel?: boolean;
  hideSessionDiscovery?: boolean;
  navigationOnly?: boolean;
  showSessionHistory?: boolean;
}

export function SessionListContent({
  user,
  onCollapse,
  onNewSession,
  onOpenSearch,
  onSetScrollEl,
  sessions,
  globalSessions,
  isLoading,
  globalIsLoading,
  hasMore,
  isLoadingMore,
  loadMoreRef,
  onUpdateSession,
  onRenameSession,
  onRenameGlobalSession,
  onSelectGlobalSession,
  onUpdateGlobalSession,
  currentSessionId,
  unreadBySession,
  sessionActions,
  isChatsCollapsed,
  onToggleChatsCollapsed,
  agentWorkspace,
  agentHistoryInMainPanel = false,
  hideSessionDiscovery = false,
  navigationOnly = false,
  showSessionHistory = false,
}: SessionListContentProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const activeNavItem = getWorkbenchNavItemFromPathname(location.pathname);
  const isAiAdmin = isAiAdminUser(
    user ? { roles: user.roles ?? [], is_admin: user.is_admin } : null,
  );
  const skillsNavigationLabel = isAiAdmin
    ? t("nav.skillManagement")
    : t("skills.available.title");

  const chatsUnreadCount = getUnreadCount({
    loadedSessions: sessions,
    unreadBySession,
  });
  const globalChatsUnreadCount = getUnreadCount({
    loadedSessions: globalSessions,
    unreadBySession,
  });
  const groupedSessions = groupSessionsByTime(sessions, t);
  const groupedAgentSessions = useMemo(
    () => groupSessionsByAgent(globalSessions),
    [globalSessions],
  );
  const [expandedAgentGroups, setExpandedAgentGroups] = useState<Set<string>>(
    () => new Set(),
  );
  const [mobileHistoryView, setMobileHistoryView] = useState<"tasks" | "all">("tasks");
  const autoExpandedSessionRef = useRef<string | null>(null);
  const autoExpandedAgentGroupsRef = useRef<string | null>(null);

  useEffect(() => {
    if (agentWorkspace || !currentSessionId) {
      autoExpandedSessionRef.current = null;
      return;
    }
    if (autoExpandedSessionRef.current === currentSessionId) return;
    const activeGroup = groupedAgentSessions.find((group) =>
      group.sessions.some((session) => session.id === currentSessionId),
    );
    if (!activeGroup) return;
    autoExpandedSessionRef.current = currentSessionId;
    setExpandedAgentGroups((previous) =>
      previous.has(activeGroup.key)
        ? previous
        : new Set(previous).add(activeGroup.key),
    );
  }, [agentWorkspace, currentSessionId, groupedAgentSessions]);

  useEffect(() => {
    const agentId = agentWorkspace?.agent_id;
    if (
      !agentId ||
      !agentHistoryInMainPanel ||
      globalIsLoading ||
      groupedAgentSessions.length === 0 ||
      autoExpandedAgentGroupsRef.current === agentId
    ) return;
    autoExpandedAgentGroupsRef.current = agentId;
    setExpandedAgentGroups(new Set(groupedAgentSessions.map((group) => group.key)));
  }, [agentHistoryInMainPanel, agentWorkspace?.agent_id, globalIsLoading, groupedAgentSessions]);

  const toggleAgentGroup = (key: string) => {
    setExpandedAgentGroups((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const renderSession = (
    session: BackendSession,
    onSelect: () => void = () => sessionActions.onSelectSession(session.id),
    onUpdate: (session: BackendSession) => void = onUpdateSession,
    onRename:
      | ((sessionId: string, title: string) => Promise<BackendSession | void>)
      | undefined = onRenameSession,
  ) => {
    if (!session.id) return null;
    return (
      <SessionItem
        key={session.id}
        session={session}
        isActive={currentSessionId === session.id}
        onSelect={onSelect}
        onDelete={() => sessionActions.onDeleteSession(session.id)}
        onSessionUpdate={onUpdate}
        onRenameSession={onRename}
      />
    );
  };
  const renderGlobalSessionGroups = () => (
    <ThreadList>
      {groupedAgentSessions.map((group) => {
        const isExpanded = expandedAgentGroups.has(group.key);
        return (
          <div key={group.key} data-agent-history-group>
            <Button
              type="text"
              htmlType="button"
              aria-expanded={isExpanded}
              onClick={() => toggleAgentGroup(group.key)}
              className="group flex h-10 w-full items-center gap-2 rounded-lg px-[9px] text-left transition-colors hover:bg-[var(--theme-sidebar-panel-muted)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)]"
            >
              <AgentIdentityAvatar
                agentId={group.identity?.agent_id ?? group.key}
                avatarRef={group.identity?.avatar_ref}
                avatarSeed={group.identity?.avatar_seed}
                name={group.name}
                size="sm"
              />
              <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-[var(--theme-text-secondary)] group-hover:text-[var(--theme-text)]">
                {group.name}
              </span>
              <span className="shrink-0 text-[11px] text-[var(--theme-text-tertiary)]">
                {group.sessions.length}
              </span>
              <ChevronDown
                size={14}
                aria-hidden="true"
                className={`shrink-0 text-[var(--theme-text-tertiary)] transition-transform duration-200 ${
                  isExpanded ? "" : "-rotate-90"
                }`}
              />
            </Button>
            {isExpanded ? (
              <div className="ml-3 border-l border-[var(--theme-border)]/70 pl-1">
                {group.sessions.map((session) =>
                  renderSession(
                    session,
                    () => onSelectGlobalSession(session),
                    onUpdateGlobalSession,
                    onRenameGlobalSession,
                  ),
                )}
              </div>
            ) : null}
          </div>
        );
      })}
    </ThreadList>
  );
  const renderTaskSessionGroups = () => (
    <ThreadList>
      {groupedSessions.map((group) => (
        <div key={group.label}>
          <div className="flex h-7 select-none items-center px-[9px] text-[13px] font-medium text-[var(--theme-text-tertiary)]">
            {group.label}
          </div>
          <div className="flex flex-col gap-px">
            {group.sessions.map((session) => renderSession(session))}
          </div>
        </div>
      ))}
    </ThreadList>
  );
  const renderLoadingSessions = () => (
    <div className="space-y-px px-0">
      {Array.from({ length: 5 }).map((_, i) => (
        <div
          key={i}
          className="flex h-10 items-center gap-2 rounded-lg px-[9px]"
        >
          <Skeleton animated="sweep"
            className="h-[13px] flex-1 rounded-md"
            style={{ height: undefined,
              width: i === 0 ? "70%" : i === 1 ? "85%" : i === 2 ? "55%" : "65%",
            }}
          />
        </div>
      ))}
    </div>
  );
  const renderTaskHistory = () => (
    <>
      {renderTaskSessionGroups()}
      {hasMore ? (
        <div ref={loadMoreRef} className="flex justify-center py-2">
          {isLoadingMore ? (
            <div className="flex items-center gap-2 text-[var(--theme-text-tertiary)]">
              <Spin size={12} />
              <span className="text-xs">{t("common.loading")}</span>
            </div>
          ) : null}
        </div>
      ) : null}
    </>
  );
  const renderHistoryCatalog = (
    label: string,
    loading: boolean,
    hasHistory: boolean,
    unreadCount: number,
    content: ReactNode,
  ) => {
    if (!hasHistory && !loading) return null;
    return (
      <>
        <div
          onClick={onToggleChatsCollapsed}
          className="group/section flex h-8 cursor-pointer select-none items-center justify-between px-[9px]"
        >
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--theme-text-tertiary)] transition-colors group-hover/section:text-[var(--theme-text-secondary)]">
              {label}
            </span>
            {unreadCount > 0 ? (
              <span className="inline-flex h-4 min-w-[16px] items-center justify-center rounded-full bg-[var(--theme-danger)] px-1 text-[10px] font-medium leading-none text-[var(--theme-primary-foreground)]">
                {formatUnreadCount(unreadCount)}
              </span>
            ) : null}
          </div>
          <ChevronDown
            size={14}
            className={`text-[var(--theme-text-tertiary)] transition-transform duration-200 ${
              isChatsCollapsed ? "-rotate-90" : ""
            }`}
          />
        </div>
        {!isChatsCollapsed ? (
          loading ? renderLoadingSessions() : content
        ) : null}
      </>
    );
  };
  const showSessionRegion = !navigationOnly || showSessionHistory;
  const taskNavItems: Array<{
    key: WorkbenchNavItem;
    icon: React.ComponentType<{ size?: number }>;
    label: string;
    onClick: () => void;
  }> = [
    {
      key: "chat",
      icon: MessageSquare,
      label: t("nav.chat"),
      onClick: () => navigate("/chat"),
    },
    {
      key: "apps",
      icon: LayoutGrid,
      label: t("nav.apps"),
      onClick: () => navigate("/apps"),
    },
    {
      key: "agentMarket",
      icon: Bot,
      label: "专家市场",
      onClick: () => navigate("/agent-market"),
    },
    ...(isAiAdmin
      ? [
          {
            key: "agentBuilder" as const,
            icon: Bot,
            label: "专家管理",
            onClick: () => navigate("/agent-builder"),
          },
        ]
      : []),
    {
      key: "skills",
      icon: Package,
      label: skillsNavigationLabel,
      onClick: () => navigate("/skills"),
    },
    {
      key: "pluginMarket",
      icon: Plug,
      label: "插件市场",
      onClick: () => navigate("/plugins"),
    },
  ];
  const governanceNavItems: Array<{
    key: WorkbenchNavItem;
    icon: React.ComponentType<{ size?: number }>;
    label: string;
    onClick: () => void;
  }> = [
    {
      key: "mcp",
      icon: Server,
      label: t("featureMenu.mcpTools"),
      onClick: () => navigate("/mcp"),
    },
    {
      key: "models",
      icon: Cpu,
      label: t("nav.models"),
      onClick: () => navigate("/models"),
    },
    {
      key: "runs",
      icon: Activity,
      label: t("nav.runs"),
      onClick: () => navigate("/runs"),
    },
  ];
  const visibleGovernanceNavItems = governanceNavItems.filter(({ key }) =>
    canAccessWorkbenchItem(user, key),
  );

  return (
    <div
      data-workbench-sidebar-panel
      data-librechat-expanded-panel="sidebar"
      className="flex h-full min-h-0 flex-col bg-[var(--theme-sidebar-panel)] text-[var(--theme-text)]"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 pb-1 pt-3 sm:px-4">
        <div className="flex h-7 min-w-0 items-center gap-2">
          <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-[var(--theme-workbench-panel)] text-[var(--theme-text)] shadow-sm ring-1 ring-[var(--theme-border)]">
            <Bot size={15} strokeWidth={2.2} aria-hidden="true" />
          </span>
          <span className="min-w-0 truncate text-[15px] font-semibold leading-none text-[var(--theme-text)]">
            {APP_NAME}
          </span>
        </div>
        <Button type="text"
          size="small"
          onClick={onCollapse}
          aria-label={t("sidebar.collapseSidebar")}
          icon={<PanelLeftClose size={18} />}
          className="shrink-0 text-[var(--theme-text-secondary)]"
          title={t("sidebar.collapseSidebar")}
        />
      </div>

      {/* Primary navigation */}
      <div
        data-workbench-primary-nav
        className="flex flex-col gap-2 px-2 pb-2 pt-2"
      >
        {!navigationOnly ? <div
          className={agentHistoryInMainPanel ? "space-y-1 xl:hidden" : "space-y-1"}
        >
          <Button type="text"
            onClick={onNewSession}
            className="sidebar-nav-btn group flex h-9 w-full items-center gap-3 rounded-md px-[9px] text-sm font-medium transition-colors focus:outline-none"
          >
            <MessageSquarePlus size={19} />
            <span className="flex-1 text-left">
              {agentWorkspace ? "开始新任务" : t("sidebar.newChat")}
            </span>
            <kbd className="hidden items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] font-medium text-[var(--theme-text-tertiary)] opacity-0 transition-opacity group-hover:opacity-100 sm:inline-flex">
              {t("sidebar.newChatShortcut")}
            </kbd>
          </Button>

          {!hideSessionDiscovery ? (
            <Button type="text"
              onClick={onOpenSearch}
              className="sidebar-nav-btn group flex h-9 w-full items-center gap-3 rounded-md px-[9px] text-sm transition-colors focus:outline-none"
            >
              <Search size={19} />
              <span className="flex-1 text-left">
                {t("sidebar.searchSessions")}
              </span>
              <kbd
                className="hidden items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] font-medium opacity-0 transition-opacity group-hover:opacity-100 sm:inline-flex"
                style={{ color: "var(--theme-text-tertiary)" }}
              >
                ⌘K
              </kbd>
            </Button>
          ) : null}
        </div> : null}

        {!navigationOnly ? (
          <div
            className={agentHistoryInMainPanel ? "h-px bg-[var(--theme-border)]/70 xl:hidden" : "h-px bg-[var(--theme-border)]/70"}
          />
        ) : null}

        <LibreChatPanelSection group="tasks" label={t("sidebar.tasks")}>
          {taskNavItems
            .filter(({ key }) => canAccessWorkbenchItem(user, key))
            .map(({ key, icon: Icon, label, onClick }) => {
              const isActive = activeNavItem === key;
              return (
                <Button type="text"
                  key={key}
                  onClick={onClick}
                  aria-current={isActive ? "page" : undefined}
                  data-active={isActive ? "true" : "false"}
                  data-workbench-nav-item={key}
                  className="sidebar-nav-btn flex h-9 w-full items-center gap-3 rounded-md px-[9px] text-sm transition-colors focus:outline-none"
                >
                  <Icon size={19} />
                  <span className="min-w-0 truncate">{label}</span>
                </Button>
              );
            })}
        </LibreChatPanelSection>

        <LibreChatPanelSection
          group="governance"
          label={t("sidebar.governance")}
        >
          {visibleGovernanceNavItems.map(
            ({ key, icon: Icon, label, onClick }) => {
            const isActive = activeNavItem === key;
            return (
              <Button type="text"
                key={key}
                onClick={onClick}
                aria-current={isActive ? "page" : undefined}
                data-active={isActive ? "true" : "false"}
                data-workbench-nav-item={key}
                className="sidebar-nav-btn flex h-9 w-full items-center gap-3 rounded-md px-[9px] text-sm transition-colors focus:outline-none"
              >
                <Icon size={19} />
                <span className="min-w-0 truncate">{label}</span>
              </Button>
            );
            },
          )}
        </LibreChatPanelSection>
      </div>

      {/* Session list */}
      {showSessionRegion ? <div
        ref={onSetScrollEl}
        data-workbench-session-region
        data-sidebar-scroll
        className="flex-1 overflow-y-auto border-t border-[var(--theme-border)]/70 px-2 pt-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        <div className="flex flex-col gap-px">
          {agentWorkspace && agentHistoryInMainPanel ? (
            <>
              <div className="hidden xl:block" data-workbench-global-history>
                {renderHistoryCatalog(
                  t("sidebar.chats"),
                  globalIsLoading,
                  groupedAgentSessions.length > 0,
                  globalChatsUnreadCount,
                  renderGlobalSessionGroups(),
                )}
              </div>
              <div className="xl:hidden" data-workbench-mobile-history>
                <div
                  aria-label="历史范围"
                  className="mb-2 grid grid-cols-2 rounded-md bg-[var(--theme-sidebar-panel-muted)] p-0.5"
                  role="group"
                >
                  <Button type="text"
                    aria-pressed={mobileHistoryView === "tasks"}
                    className={`rounded px-2 py-1.5 text-xs font-medium transition-colors ${
                      mobileHistoryView === "tasks"
                        ? "bg-[var(--theme-workbench-panel)] text-[var(--theme-text)] shadow-sm"
                        : "text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)]"
                    }`}
                    onClick={() => setMobileHistoryView("tasks")}
                    htmlType="button"
                  >
                    任务历史
                  </Button>
                  <Button type="text"
                    aria-pressed={mobileHistoryView === "all"}
                    className={`rounded px-2 py-1.5 text-xs font-medium transition-colors ${
                      mobileHistoryView === "all"
                        ? "bg-[var(--theme-workbench-panel)] text-[var(--theme-text)] shadow-sm"
                        : "text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)]"
                    }`}
                    onClick={() => setMobileHistoryView("all")}
                    htmlType="button"
                  >
                    全部对话
                  </Button>
                </div>
                {mobileHistoryView === "tasks"
                  ? renderHistoryCatalog(
                      "任务历史",
                      isLoading,
                      groupedSessions.length > 0,
                      chatsUnreadCount,
                      renderTaskHistory(),
                    )
                  : renderHistoryCatalog(
                      t("sidebar.chats"),
                      globalIsLoading,
                      groupedAgentSessions.length > 0,
                      globalChatsUnreadCount,
                      renderGlobalSessionGroups(),
                    )}
              </div>
            </>
          ) : renderHistoryCatalog(
            agentWorkspace ? "任务历史" : t("sidebar.chats"),
            agentWorkspace ? isLoading : globalIsLoading,
            agentWorkspace
              ? groupedSessions.length > 0
              : groupedAgentSessions.length > 0,
            agentWorkspace ? chatsUnreadCount : globalChatsUnreadCount,
            agentWorkspace ? renderTaskHistory() : renderGlobalSessionGroups(),
          )}
        </div>
      </div> : <div className="flex-1" data-workbench-navigation-spacer />}

      <div className="mt-auto flex items-center justify-between border-t border-[var(--theme-border)] px-3 py-3">
        <div className="min-w-0 flex-1"><UserMenu showLabel /></div>
          <Button type="text"
            aria-label="设置"
            size="small"
            icon={<Settings aria-hidden="true" size={18} />}
            className="shrink-0 text-[var(--theme-text-secondary)]"
            onClick={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              window.dispatchEvent(
                new CustomEvent("workbench-menu-open", {
                  detail: {
                    bottom: window.innerHeight - rect.top + 8,
                    right: window.innerWidth - rect.right,
                  },
                }),
              );
            }}
            title="设置"
            htmlType="button"
          />
        </div>

    </div>
  );
}
