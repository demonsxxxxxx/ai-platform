import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { SessionSidebar } from "../../panels/SessionSidebar";
import { AppShell } from "./AppShell";
import { TabContent } from "./TabContent";
import type { RouteUnavailableConfig, TabType } from "./types";

export interface NonChatAppContentProps {
  activeTab: Exclude<TabType, "chat">;
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
  mobileSidebarOpen: boolean;
  setMobileSidebarOpen: (open: boolean) => void;
  routeUnavailable?: RouteUnavailableConfig;
}

export function NonChatAppContent({
  activeTab,
  sidebarCollapsed,
  setSidebarCollapsed,
  mobileSidebarOpen,
  setMobileSidebarOpen,
  routeUnavailable,
}: NonChatAppContentProps) {
  const navigate = useNavigate();

  const handleSelectSession = useCallback(
    (id: string) => {
      setMobileSidebarOpen(false);
      navigate(`/chat/${id}`);
    },
    [navigate, setMobileSidebarOpen],
  );
  const handleNewSession = useCallback(() => {
    setMobileSidebarOpen(false);
    navigate("/chat");
  }, [navigate, setMobileSidebarOpen]);
  const handleMobileClose = useCallback(
    () => setMobileSidebarOpen(false),
    [setMobileSidebarOpen],
  );

  return (
    <AppShell
      activeTab={activeTab}
      setMobileSidebarOpen={setMobileSidebarOpen}
      currentProjectId={null}
      projectManager={{ projects: [] }}
      onNewSession={handleNewSession}
      sidebar={
        <SessionSidebar
          currentSessionId={null}
          onSelectSession={handleSelectSession}
          onNewSession={handleNewSession}
          mobileOpen={mobileSidebarOpen}
          onMobileOpen={() => setMobileSidebarOpen(true)}
          onMobileClose={handleMobileClose}
          isCollapsed={sidebarCollapsed}
          onToggleCollapsed={setSidebarCollapsed}
        />
      }
    >
      <TabContent activeTab={activeTab} routeUnavailable={routeUnavailable} />
    </AppShell>
  );
}
