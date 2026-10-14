import { useCallback, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { AppShell } from "../../components/layout/AppContent/AppShell";
import { SessionSidebar } from "../../components/panels/SessionSidebar";
import { useSidebarPreference } from "../../hooks/useSidebarPreference";

/** Keep the administrative editor inside the same persistent application shell. */
export function AgentBuilderShell({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [sidebarCollapsed, handleSetSidebarCollapsed] = useSidebarPreference();

  const handleSelectSession = useCallback(
    (_sessionId: string) => {
      setMobileSidebarOpen(false);
      navigate("/agent-market");
    },
    [navigate],
  );
  const handleNewSession = useCallback(() => {
    setMobileSidebarOpen(false);
    navigate("/agent-market");
  }, [navigate]);

  return (
    <AppShell
      activeTab="chat"
      onNewSession={handleNewSession}
      allowNewSessionAction={false}
      sidebar={
        <SessionSidebar
          currentSessionId={null}
          isCollapsed={sidebarCollapsed}
          mobileOpen={mobileSidebarOpen}
          onMobileClose={() => setMobileSidebarOpen(false)}
          onMobileOpen={() => setMobileSidebarOpen(true)}
          onNewSession={handleNewSession}
          onSelectSession={handleSelectSession}
          onToggleCollapsed={handleSetSidebarCollapsed}
          navigationOnly
        />
      }
    >
      {children}
    </AppShell>
  );
}
