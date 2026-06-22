import { MessageSquarePlus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { WorkbenchUnavailableState } from "../workbench/WorkbenchUnavailableState";

export function ChannelImportPanel() {
  const { t } = useTranslation();
  const backedSources: Array<{
    id: string;
    name: string;
    redaction: string;
    retention: string;
  }> = [];
  const importMetadataLabels = {
    redaction: t("channelImport.redaction"),
    retention: t("channelImport.retention"),
  };
  void importMetadataLabels;

  if (backedSources.length === 0) {
    return (
      <div
        data-phase1c-surface="channel-import"
        className="flex h-full min-h-0 items-center justify-center p-6"
      >
        <WorkbenchUnavailableState
          surface="channel-import-projection"
          title={t("channelImport.unavailable.title")}
          description={t("channelImport.unavailable.description")}
        />
      </div>
    );
  }

  return (
    <div
      data-phase1c-surface="channel-import"
      className="flex h-full min-h-0 flex-col gap-3 p-4"
    >
      {backedSources.map((source) => (
        <section
          key={source.id}
          className={`${workbenchSurface.compactPanel} p-3`}
        >
          <div className="flex items-center gap-2">
            <MessageSquarePlus size={16} />
            <h2 className="text-sm font-semibold">{source.name}</h2>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            {t("channelImport.redaction")}: {source.redaction}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {t("channelImport.retention")}: {source.retention}
          </p>
        </section>
      ))}
    </div>
  );
}
