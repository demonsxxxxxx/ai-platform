import { Suspense, lazy } from "react";
import { WorkbenchUnavailableState } from "../../workbench/WorkbenchUnavailableState";
import type { RouteUnavailableConfig, TabType } from "./types";

const SkillsHubPanel = lazy(() =>
  import("../../panels/SkillsHubPanel").then((m) => ({
    default: m.SkillsHubPanel,
  })),
);
const UsersPanel = lazy(() =>
  import("../../panels/UsersPanel").then((m) => ({ default: m.UsersPanel })),
);
const RolesPanel = lazy(() =>
  import("../../panels/RolesPanel").then((m) => ({ default: m.RolesPanel })),
);
const SettingsPanel = lazy(() =>
  import("../../panels/SettingsPanel").then((m) => ({
    default: m.SettingsPanel,
  })),
);
const AgentConfigPanel = lazy(() =>
  import("../../panels/AgentPanel").then((m) => ({
    default: m.AgentConfigPanel,
  })),
);
const MCPPanel = lazy(() =>
  import("../../panels/MCPPanel").then((m) => ({ default: m.MCPPanel })),
);
const FeedbackPanel = lazy(() =>
  import("../../panels/FeedbackPanel").then((m) => ({
    default: m.FeedbackPanel,
  })),
);
const QuarantinedLegacyPanel = lazy(() =>
  import("./QuarantinedLegacyPanel").then((m) => ({
    default: m.QuarantinedLegacyPanel,
  })),
);
const ChannelImportPanel = lazy(() =>
  import("../../channels/ChannelImportPanel").then((m) => ({
    default: m.ChannelImportPanel,
  })),
);
const RevealedFilesPage = lazy(() =>
  import("../../fileLibrary/RevealedFilesPanel").then((m) => ({
    default: m.RevealedFilesPanel,
  })),
);
const NotificationPanel = lazy(() =>
  import("../../panels/NotificationPanel").then((m) => ({
    default: m.NotificationPanel,
  })),
);
const MemoryPanel = lazy(() =>
  import("../../panels/MemoryPanel").then((m) => ({
    default: m.MemoryPanel,
  })),
);
const LaunchpadPanel = lazy(() =>
  import("../../launchpad").then((m) => ({
    default: m.LaunchpadPanel,
  })),
);

const panelMap: Record<
  string,
  React.LazyExoticComponent<React.ComponentType>
> = {
  apps: LaunchpadPanel,
  skills: SkillsHubPanel,
  marketplace: SkillsHubPanel,
  users: UsersPanel,
  roles: RolesPanel,
  settings: SettingsPanel,
  mcp: MCPPanel,
  feedback: FeedbackPanel,
  channels: ChannelImportPanel,
  agents: AgentConfigPanel,
  models: QuarantinedLegacyPanel,
  files: RevealedFilesPage,
  notifications: NotificationPanel,
  memory: MemoryPanel,
};

function PanelLoader() {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="relative h-8 w-8">
        <div className="absolute inset-0 rounded-full border-2 border-stone-200 dark:border-stone-700" />
        <div className="absolute inset-0 rounded-full border-2 border-transparent border-t-stone-500 dark:border-t-stone-400 animate-spin will-change-transform" />
      </div>
    </div>
  );
}

export function TabContent({
  activeTab,
  routeUnavailable,
}: {
  activeTab: TabType;
  routeUnavailable?: RouteUnavailableConfig;
}) {
  if (activeTab === "chat") return null;

  if (routeUnavailable) {
    return (
      <main
        className="flex-1 overflow-hidden bg-[var(--theme-bg)]"
        data-authenticated-workbench-page={activeTab}
        data-frontend-governance-state={routeUnavailable.state}
      >
        <div className="flex h-full w-full items-center justify-center px-4">
          <WorkbenchUnavailableState
            title={routeUnavailable.title}
            description={routeUnavailable.description}
            surface={routeUnavailable.surface}
          />
        </div>
      </main>
    );
  }

  const Panel = panelMap[activeTab];
  if (!Panel) return null;

  return (
    <main
      className="flex-1 overflow-hidden bg-[var(--theme-bg)]"
      data-authenticated-workbench-page={activeTab}
    >
      <div className="flex h-full w-full flex-col">
        <Suspense fallback={<PanelLoader />}>
          <Panel />
        </Suspense>
      </div>
    </main>
  );
}
