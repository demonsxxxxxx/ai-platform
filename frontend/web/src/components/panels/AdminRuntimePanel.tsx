import { Activity } from "lucide-react";
import { useTranslation } from "react-i18next";
import { PanelHeader } from "../common/PanelHeader";
import { AdminRuntimeCapacitySection } from "./AdminRuntimeCapacitySection";
import { SystemHealthSection } from "./SystemHealthSection";

export function AdminRuntimePanel() {
  const { t } = useTranslation();

  return (
    <div className="glass-shell flex h-full min-h-0 flex-col">
      <PanelHeader
        title={t("adminRuntime.panelTitle", "Admin Runtime")}
        subtitle={t(
          "adminRuntime.panelSubtitle",
          "Queue, capacity, backpressure, and runtime health projections",
        )}
        icon={<Activity size={20} />}
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        <SystemHealthSection />
        <AdminRuntimeCapacitySection />
      </div>
    </div>
  );
}
