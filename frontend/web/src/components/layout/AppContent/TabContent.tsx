import { Suspense, lazy } from "react";
import type { TabType } from "./types";
import { getSurfacePolicy } from "./phase1SurfacePolicy";

const AdminRuntimePanel = lazy(() =>
  import("../../panels/AdminRuntimePanel").then((m) => ({
    default: m.AdminRuntimePanel,
  })),
);
const MemoryPanel = lazy(() =>
  import("../../panels/MemoryPanel").then((m) => ({
    default: m.MemoryPanel,
  })),
);
const Phase2UnavailablePanel = lazy(() =>
  import("./Phase2UnavailablePanel").then((m) => ({
    default: m.Phase2UnavailablePanel,
  })),
);
const Phase1SkillsGovernancePanel = lazy(() =>
  import("../../panels/phase1ProjectionPanels").then((m) => ({
    default: m.Phase1SkillsGovernancePanel,
  })),
);
const Phase1ToolPolicyPanel = lazy(() =>
  import("../../panels/phase1ProjectionPanels").then((m) => ({
    default: m.Phase1ToolPolicyPanel,
  })),
);
const Phase1AgentAppsPanel = lazy(() =>
  import("../../panels/phase1ProjectionPanels").then((m) => ({
    default: m.Phase1AgentAppsPanel,
  })),
);
const Phase1ModelCatalogPanel = lazy(() =>
  import("../../panels/phase1ProjectionPanels").then((m) => ({
    default: m.Phase1ModelCatalogPanel,
  })),
);
const Phase1NotificationsPanel = lazy(() =>
  import("../../panels/phase1ProjectionPanels").then((m) => ({
    default: m.Phase1NotificationsPanel,
  })),
);

const panelMap: Record<
  string,
  React.LazyExoticComponent<React.ComponentType>
> = {
  skills: Phase1SkillsGovernancePanel,
  mcp: Phase1ToolPolicyPanel,
  agents: Phase1AgentAppsPanel,
  models: Phase1ModelCatalogPanel,
  notifications: Phase1NotificationsPanel,
  settings: AdminRuntimePanel,
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

export function TabContent({ activeTab }: { activeTab: TabType }) {
  if (activeTab === "chat") return null;

  const policy = getSurfacePolicy(activeTab);
  const Panel = panelMap[activeTab];

  return (
    <main className="flex-1 overflow-hidden">
      <div className="mx-auto max-w-4xl sm:max-w-5xl lg:max-w-6xl w-full h-full flex flex-col">
        <Suspense fallback={<PanelLoader />}>
          {policy.render === "phase2-unavailable" ? (
            <Phase2UnavailablePanel tab={activeTab} />
          ) : Panel ? (
            <Panel />
          ) : null}
        </Suspense>
      </div>
    </main>
  );
}
