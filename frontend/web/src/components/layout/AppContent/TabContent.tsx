import { Suspense, lazy } from "react";
import { GovernedRouteWorkbench } from "../../workbench/GovernedRouteWorkbench";
import type { RouteUnavailableConfig, TabType } from "./types";

const SkillsHubPanel = lazy(() =>
  import("../../panels/SkillsHubPanel").then((m) => ({
    default: m.SkillsHubPanel,
  })),
);
const RolesPanel = lazy(() =>
  import("../../panels/RolesPanel").then((m) => ({ default: m.RolesPanel })),
);
const MCPPanel = lazy(() =>
  import("../../panels/MCPPanel").then((m) => ({ default: m.MCPPanel })),
);
const ModelCatalogPanel = lazy(() =>
  import("../../panels/ModelCatalogPanel").then((m) => ({
    default: m.ModelCatalogPanel,
  })),
);
const ChannelImportPanel = lazy(() =>
  import("../../channels/ChannelImportPanel").then((m) => ({
    default: m.ChannelImportPanel,
  })),
);
const MemoryPanel = lazy(() =>
  import("../../panels/MemoryPanel").then((m) => ({
    default: m.MemoryPanel,
  })),
);
const PersonaWorkbenchPanel = lazy(() =>
  import("../../persona/PersonaWorkbenchPanel").then((m) => ({
    default: m.PersonaWorkbenchPanel,
  })),
);
const RevealedFilesWorkbenchPanel = lazy(() =>
  import("../../fileLibrary/RevealedFilesWorkbenchPanel").then((m) => ({
    default: m.RevealedFilesWorkbenchPanel,
  })),
);
const AgentDirectoryPanel = lazy(() =>
  import("../../panels/AgentDirectoryPanel").then((m) => ({
    default: m.AgentDirectoryPanel,
  })),
);
const LaunchpadPanel = lazy(() =>
  import("../../launchpad").then((m) => ({
    default: m.LaunchpadPanel,
  })),
);
const WorkbenchUsersProjectionPanel = lazy(() =>
  import("../../workbench/WorkbenchProjectionPages").then((m) => ({
    default: m.WorkbenchUsersProjectionPanel,
  })),
);
const WorkbenchSettingsProjectionPanel = lazy(() =>
  import("../../workbench/WorkbenchProjectionPages").then((m) => ({
    default: m.WorkbenchSettingsProjectionPanel,
  })),
);
const WorkbenchFeedbackProjectionPanel = lazy(() =>
  import("../../workbench/WorkbenchProjectionPages").then((m) => ({
    default: m.WorkbenchFeedbackProjectionPanel,
  })),
);
const WorkbenchNotificationsProjectionPanel = lazy(() =>
  import("../../workbench/WorkbenchProjectionPages").then((m) => ({
    default: m.WorkbenchNotificationsProjectionPanel,
  })),
);

const panelMap: Record<
  string,
  React.LazyExoticComponent<React.ComponentType>
> = {
  apps: LaunchpadPanel,
  skills: SkillsHubPanel,
  marketplace: SkillsHubPanel,
  users: WorkbenchUsersProjectionPanel,
  roles: RolesPanel,
  settings: WorkbenchSettingsProjectionPanel,
  mcp: MCPPanel,
  feedback: WorkbenchFeedbackProjectionPanel,
  channels: ChannelImportPanel,
  agents: AgentDirectoryPanel,
  models: ModelCatalogPanel,
  persona: PersonaWorkbenchPanel,
  files: RevealedFilesWorkbenchPanel,
  notifications: WorkbenchNotificationsProjectionPanel,
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
        <GovernedRouteWorkbench
          activeTab={activeTab}
          config={routeUnavailable}
        />
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
