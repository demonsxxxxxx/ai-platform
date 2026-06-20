import { MessageSquarePlus, ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { workbenchSurface } from "../workbench/workbenchSurface";

export function ChannelImportPanel() {
  const { t } = useTranslation();
  const backedSources: Array<{
    id: string;
    name: string;
    redaction: string;
    retention: string;
  }> = [];

  if (backedSources.length === 0) {
    return (
      <div className="flex h-full min-h-0 items-center justify-center p-6">
        <section
          className={`${workbenchSurface.compactPanel} max-w-xl p-5 text-center`}
        >
          <ShieldAlert className="mx-auto text-slate-500" size={32} />
          <h2 className="mt-4 text-base font-semibold text-slate-900 dark:text-stone-100">
            {t("channelImport.unavailable.title")}
          </h2>
          <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-stone-300">
            {t("channelImport.unavailable.description")}
          </p>
        </section>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
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
