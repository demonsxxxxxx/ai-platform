import { CircleOff } from "lucide-react";
import { useTranslation } from "react-i18next";
import { PanelHeader } from "../common/PanelHeader";
import { workbenchSurface } from "./workbenchSurface";

/** Static destination for retained deep-links to unimplemented features. */
export function WorkbenchUnavailablePanel({
  feature,
}: {
  feature: "feedback" | "notifications";
}) {
  const { t } = useTranslation();

  return (
    <div data-workbench-unavailable={feature} className={workbenchSurface.page}>
      <PanelHeader
        title={t(`workbench.unavailable.${feature}.title`)}
        icon={<CircleOff size={20} className="text-theme-text-secondary" />}
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <p className={workbenchSurface.unavailable}>
          {t(`workbench.unavailable.${feature}.description`)}
        </p>
      </div>
    </div>
  );
}
