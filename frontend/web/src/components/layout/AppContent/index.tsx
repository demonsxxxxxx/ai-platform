import { useEffect, useState } from "react";
import { useSidebarPreference } from "../../../hooks/useSidebarPreference";
import { ChatAppContent } from "./ChatAppContent";
import { NonChatAppContent } from "./NonChatAppContent";
import {
  APP_TOAST_SIDEBAR_OFFSET_VAR,
  getAppToastSidebarOffset,
} from "./appToastLayout";
import type { TabType } from "./types";
import type { RouteUnavailableConfig } from "./types";

interface AppContentProps {
  activeTab: TabType;
  routeUnavailable?: RouteUnavailableConfig;
}

export function AppContent({ activeTab, routeUnavailable }: AppContentProps) {
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [sidebarCollapsed, handleSetSidebarCollapsed] = useSidebarPreference();

  useEffect(() => {
    if (typeof document === "undefined") return undefined;

    const rootStyle = document.documentElement.style;
    rootStyle.setProperty(
      APP_TOAST_SIDEBAR_OFFSET_VAR,
      getAppToastSidebarOffset({ sidebarCollapsed }),
    );

    return () => {
      rootStyle.removeProperty(APP_TOAST_SIDEBAR_OFFSET_VAR);
    };
  }, [sidebarCollapsed]);

  if (activeTab === "chat") {
    return (
      <ChatAppContent
        sidebarCollapsed={sidebarCollapsed}
        setSidebarCollapsed={handleSetSidebarCollapsed}
        mobileSidebarOpen={mobileSidebarOpen}
        setMobileSidebarOpen={setMobileSidebarOpen}
      />
    );
  }

  return (
    <NonChatAppContent
      activeTab={activeTab}
      sidebarCollapsed={sidebarCollapsed}
      setSidebarCollapsed={handleSetSidebarCollapsed}
      mobileSidebarOpen={mobileSidebarOpen}
      setMobileSidebarOpen={setMobileSidebarOpen}
      routeUnavailable={routeUnavailable}
    />
  );
}
