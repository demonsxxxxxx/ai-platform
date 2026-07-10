import {
  MessageSquarePlus,
  Search,
  Clock,
  LayoutGrid,
  Package,
  Server,
  Bot,
  Cpu,
  MessageCircle,
  UserRound,
  FileStack,
  SquareTerminal,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import {
  getWorkbenchNavItemFromPathname,
  type WorkbenchNavItem,
} from "./navigationState";
import { LibreChatRailButton } from "../../../librechat-ui/Rail";

const railBtn = "";

interface SidebarRailProps {
  user: { username?: string; avatar_url?: string } | null;
  imgError: boolean;
  onImgError: () => void;
  isExpanded?: boolean;
  onExpand: () => void;
  onCollapse: () => void;
  onNewSession: () => void;
  onOpenSearch: () => void;
  onOpenRecentChats: () => void;
  onOpenLaunchpad: () => void;
  onOpenSkills: () => void;
  onOpenMcp: () => void;
  onOpenChannels: () => void;
  onOpenAgents: () => void;
  onOpenModels: () => void;
  onOpenPersona: () => void;
  onOpenFiles: () => void;
  onOpenAgentWorkspace: () => void;
  recentChatsBtnRef: React.RefObject<HTMLButtonElement | null>;
  onShowProfile: () => void;
}

export function SidebarRail({
  user,
  imgError,
  onImgError,
  isExpanded = false,
  onExpand,
  onCollapse,
  onNewSession,
  onOpenSearch,
  onOpenRecentChats,
  onOpenLaunchpad,
  onOpenSkills,
  onOpenMcp,
  onOpenChannels,
  onOpenAgents,
  onOpenModels,
  onOpenPersona,
  onOpenFiles,
  onOpenAgentWorkspace,
  recentChatsBtnRef,
  onShowProfile,
}: SidebarRailProps) {
  const { t } = useTranslation();
  const location = useLocation();
  const activeRailItem = getWorkbenchNavItemFromPathname(location.pathname);
  const showActiveRailState = !isExpanded;
  const isRailItemActive = (item: WorkbenchNavItem) =>
    showActiveRailState && activeRailItem === item;

  return (
    <nav
      data-librechat-rail
      className="workbench-rail pointer-events-auto absolute inset-0 flex h-full w-[--sidebar-rail-width] select-none flex-col items-start border-r border-[var(--theme-border)] bg-[var(--theme-sidebar-rail)] text-[var(--theme-text-secondary)] opacity-100 transition-opacity duration-150 ease-[steps(1,end)]"
      aria-label={t("sidebarView")}
    >
      {/* Expand button — default: app icon, hover: expand icon */}
      <div className="flex items-center justify-center w-full pt-3">
        <LibreChatRailButton
          onClick={isExpanded ? onCollapse : onExpand}
          className={`${railBtn} group ${
            isExpanded
              ? "cursor-w-resize rtl:cursor-e-resize"
              : "cursor-e-resize rtl:cursor-w-resize"
          }`}
          aria-expanded={isExpanded}
          aria-label={
            isExpanded
              ? t("sidebar.collapseSidebar")
              : t("sidebar.expandSidebar")
          }
          title={
            isExpanded
              ? t("sidebar.collapseSidebar")
              : t("sidebar.expandSidebar")
          }
        >
          <span
            className={`flex size-8 items-center justify-center rounded-lg bg-[var(--theme-workbench-panel)] text-[var(--theme-text)] shadow-sm ring-1 ring-[var(--theme-border)] ${
              isExpanded ? "hidden" : "group-hover:hidden"
            }`}
          >
            <Bot size={17} strokeWidth={2.2} aria-hidden="true" />
          </span>
          <svg
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            className={`w-5 h-5 ${
              isExpanded ? "block" : "hidden group-hover:block"
            }`}
          >
            <path
              fillRule="evenodd"
              clipRule="evenodd"
              d="M8.85719 3H15.1428C16.2266 2.99999 17.1007 2.99998 17.8086 3.05782C18.5375 3.11737 19.1777 3.24318 19.77 3.54497C20.7108 4.02433 21.4757 4.78924 21.955 5.73005C22.2568 6.32234 22.3826 6.96253 22.4422 7.69138C22.5 8.39925 22.5 9.27339 22.5 10.3572V13.6428C22.5 14.7266 22.5 15.6008 22.4422 16.3086C22.3826 17.0375 22.2568 17.6777 21.955 18.27C21.4757 19.2108 20.7108 19.9757 19.77 20.455C19.1777 20.7568 18.5375 20.8826 17.8086 20.9422C17.1008 21 16.2266 21 15.1428 21H8.85717C7.77339 21 6.89925 21 6.19138 20.9422C5.46253 20.8826 4.82234 20.7568 4.23005 20.455C3.28924 19.9757 2.52433 19.2108 2.04497 18.27C1.74318 17.6777 1.61737 17.0375 1.55782 16.3086C1.49998 15.6007 1.49999 14.7266 1.5 13.6428V10.3572C1.49999 9.27341 1.49998 8.39926 1.55782 7.69138C1.61737 6.96253 1.74318 6.32234 2.04497 5.73005C2.52433 4.78924 2.52433 4.02433 4.23005 3.54497C4.82234 3.24318 5.46253 3.11737 6.19138 3.05782C6.89926 2.99998 7.77341 2.99999 8.85719 3ZM6.35424 5.05118C5.74907 5.10062 5.40138 5.19279 5.13803 5.32698C4.57354 5.6146 4.1146 6.07354 3.82698 6.63803C3.69279 6.90138 3.60062 7.24907 3.55118 7.85424C3.50078 8.47108 3.5 9.26339 3.5 10.4V13.6C3.5 14.7366 3.50078 15.5289 3.55118 16.1458C3.60062 16.7509 3.69279 17.0986 3.82698 17.362C4.1146 17.9265 4.57354 18.3854 5.13803 18.673C5.40138 18.8072 5.74907 18.8994 6.35424 18.9488C6.97108 18.9992 7.76339 19 8.9 19H9.5V5H8.9C7.76339 5 6.97108 5.00078 6.35424 5.05118ZM11.5 5V19H15.1C16.2366 19 17.0289 18.9992 17.6458 18.9488C18.2509 18.8994 18.5986 18.8072 18.862 18.673C19.4265 18.3854 19.8854 17.9265 20.173 17.362C20.3072 17.0986 20.3994 16.7509 20.4488 16.1458C20.4992 15.5289 20.5 14.7366 20.5 13.6V10.4C20.5 9.26339 20.4992 8.47108 20.4488 7.85424C20.3994 7.24907 20.3072 6.40138 20.173 6.63803C19.8854 6.57354 19.4265 6.1146 18.862 5.32698C18.5986 5.19279 18.2509 5.10062 17.6458 5.05118C17.0289 5.00078 16.2366 5 15.1 5H11.5ZM5 8.5C5 7.94772 5.44772 7.5 6 7.5H7C7.55229 7.5 8 7.94772 8 8.5C8 9.05229 7.55229 9.5 7 9.5H6C5.44772 9.5 5 9.05229 5 8.5ZM5 12C5 11.4477 5.44772 11 6 11H7C7.55229 11 8 11.4477 8 12C8 12.5523 7.55229 13 7 13H6C5.44772 13 5 12.5527 5 12Z"
              fill="currentColor"
            />
          </svg>
        </LibreChatRailButton>
      </div>

      {/* Action icons — scrollable when overflowing, no scrollbar */}
      <div
        className="mt-2 flex-1 min-h-0 overflow-y-auto overflow-x-hidden flex flex-col items-center w-full gap-0.5 py-1"
        style={{ scrollbarWidth: "none", msOverflowStyle: "none" }}
      >
        <LibreChatRailButton
          type="button"
          onClick={onNewSession}
          className={railBtn}
          title={t("sidebar.newChat")}
          aria-label={t("sidebar.newChat")}
        >
          <MessageSquarePlus size={20} />
        </LibreChatRailButton>
        <LibreChatRailButton
          type="button"
          onClick={onOpenSearch}
          className={railBtn}
          title={t("sidebar.searchSessions")}
          aria-label={t("sidebar.searchSessions")}
        >
          <Search size={20} />
        </LibreChatRailButton>
        <LibreChatRailButton
          type="button"
          onClick={onOpenLaunchpad}
          className={railBtn}
          aria-current={isRailItemActive("apps") ? "page" : undefined}
          title={t("nav.apps")}
          aria-label={t("nav.apps")}
          itemKey="apps"
          active={isRailItemActive("apps")}
        >
          <LayoutGrid size={20} />
        </LibreChatRailButton>
        <LibreChatRailButton
          type="button"
          onClick={onOpenSkills}
          className={railBtn}
          aria-current={isRailItemActive("skills") ? "page" : undefined}
          title={t("nav.skillManagement")}
          aria-label={t("nav.skillManagement")}
          itemKey="skills"
          active={isRailItemActive("skills")}
        >
          <Package size={20} />
        </LibreChatRailButton>
        <LibreChatRailButton
          type="button"
          onClick={onOpenMcp}
          className={railBtn}
          aria-current={isRailItemActive("mcp") ? "page" : undefined}
          title={t("nav.mcp")}
          aria-label={t("nav.mcp")}
          itemKey="mcp"
          active={isRailItemActive("mcp")}
        >
          <Server size={20} />
        </LibreChatRailButton>
        <LibreChatRailButton
          type="button"
          onClick={onOpenChannels}
          className={railBtn}
          aria-current={isRailItemActive("channels") ? "page" : undefined}
          title={t("nav.channels")}
          aria-label={t("nav.channels")}
          itemKey="channels"
          active={isRailItemActive("channels")}
        >
          <MessageCircle size={20} />
        </LibreChatRailButton>
        <LibreChatRailButton
          type="button"
          onClick={onOpenAgents}
          className={railBtn}
          aria-current={isRailItemActive("agents") ? "page" : undefined}
          title={t("nav.agents")}
          aria-label={t("nav.agents")}
          itemKey="agents"
          active={isRailItemActive("agents")}
        >
          <Bot size={20} />
        </LibreChatRailButton>
        <LibreChatRailButton
          type="button"
          onClick={onOpenModels}
          className={railBtn}
          aria-current={isRailItemActive("models") ? "page" : undefined}
          title={t("nav.models")}
          aria-label={t("nav.models")}
          itemKey="models"
          active={isRailItemActive("models")}
        >
          <Cpu size={20} />
        </LibreChatRailButton>
        <LibreChatRailButton
          type="button"
          onClick={onOpenPersona}
          className={railBtn}
          aria-current={isRailItemActive("persona") ? "page" : undefined}
          title={t("nav.persona")}
          aria-label={t("nav.persona")}
          itemKey="persona"
          active={isRailItemActive("persona")}
        >
          <UserRound size={20} />
        </LibreChatRailButton>
        <LibreChatRailButton
          type="button"
          onClick={onOpenFiles}
          className={railBtn}
          aria-current={isRailItemActive("files") ? "page" : undefined}
          title={t("nav.files")}
          aria-label={t("nav.files")}
          itemKey="files"
          active={isRailItemActive("files")}
        >
          <FileStack size={20} />
        </LibreChatRailButton>
        <LibreChatRailButton
          type="button"
          onClick={onOpenAgentWorkspace}
          className={railBtn}
          aria-current={
            isRailItemActive("agent-workspace") ? "page" : undefined
          }
          title={t("nav.agentWorkspace")}
          aria-label={t("nav.agentWorkspace")}
          itemKey="agent-workspace"
          active={isRailItemActive("agent-workspace")}
        >
          <SquareTerminal size={20} />
        </LibreChatRailButton>
        <LibreChatRailButton
          type="button"
          ref={recentChatsBtnRef}
          onClick={onOpenRecentChats}
          className={railBtn}
          title={t("sidebar.recentChats")}
          aria-label={t("sidebar.recentChats")}
        >
          <Clock size={20} />
        </LibreChatRailButton>
      </div>

      {/* Profile avatar */}
      <div
        className="shrink-0 py-4 border-t flex flex-col items-center w-full"
        style={{ borderColor: "var(--theme-border)" }}
      >
        <LibreChatRailButton
          onClick={onShowProfile}
          className={`${railBtn} rounded-full transition cursor-pointer`}
          aria-label={t("sidebar.expandSidebar")}
        >
          <div
            className="shrink-0 w-8 h-8 rounded-full overflow-hidden transition"
            style={{ boxShadow: "0 0 0 1px var(--theme-border)" }}
          >
            {user?.avatar_url && !imgError ? (
              <img
                src={user.avatar_url}
                alt={user?.username || "User"}
                className="w-full h-full object-cover rounded-full"
                onError={onImgError}
                draggable={false}
              />
            ) : (
              <div className="flex h-full w-full items-center justify-center rounded-full bg-[var(--theme-primary)]">
                <span className="text-xs font-semibold text-[var(--theme-primary-foreground)]">
                  {user?.username?.charAt(0).toUpperCase() || "U"}
                </span>
              </div>
            )}
          </div>
        </LibreChatRailButton>
      </div>
    </nav>
  );
}
