import { useCallback, useEffect, useState } from "react";
import { SIDEBAR_COLLAPSED_STORAGE_KEY } from "../../../hooks/useAuth";
import { authApi } from "../../../services/api";
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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    const saved = localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY);
    return saved !== null ? saved === "true" : false;
  });

  const handleSetSidebarCollapsed = useCallback(
    (collapsed: boolean | ((prev: boolean) => boolean)) => {
      setSidebarCollapsed((prev) => {
        const next =
          typeof collapsed === "function" ? collapsed(prev) : collapsed;
        localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(next));
        authApi
          .updateMetadata({ sidebarCollapsed: String(next) })
          .catch(() => {});
        return next;
      });
    },
    [],
  );

  useEffect(() => {
    const handler = (e: Event) => {
      const collapsed = (e as CustomEvent).detail as boolean;
      setSidebarCollapsed(collapsed);
    };
    window.addEventListener("sidebar-collapsed-changed", handler);
    return () =>
      window.removeEventListener("sidebar-collapsed-changed", handler);
  }, []);

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
