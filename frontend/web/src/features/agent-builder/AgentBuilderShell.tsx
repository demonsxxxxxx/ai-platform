import { useCallback, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { AppShell } from "../../components/layout/AppContent/AppShell";
import { SessionSidebar } from "../../components/panels/SessionSidebar";
import { SIDEBAR_COLLAPSED_STORAGE_KEY } from "../../hooks/useAuth";
import { authApi } from "../../services/api";

/** Keep the administrative editor inside the same persistent application shell. */
export function AgentBuilderShell({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    const saved = localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY);
    return saved !== null ? saved === "true" : false;
  });

  const handleSetSidebarCollapsed = useCallback((collapsed: boolean) => {
    setSidebarCollapsed(collapsed);
    localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(collapsed));
    authApi.updateMetadata({ sidebarCollapsed: String(collapsed) }).catch(() => {});
  }, []);
  const handleSelectSession = useCallback(
    (sessionId: string) => {
      setMobileSidebarOpen(false);
      navigate(`/chat/${encodeURIComponent(sessionId)}`);
    },
    [navigate],
  );
  const handleNewSession = useCallback(() => {
    setMobileSidebarOpen(false);
    navigate("/chat");
  }, [navigate]);

  return (
    <AppShell
      activeTab="chat"
      setMobileSidebarOpen={setMobileSidebarOpen}
      onNewSession={handleNewSession}
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
        />
      }
    >
      {children}
    </AppShell>
  );
}
