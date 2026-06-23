import {
  BellRing,
  Cable,
  Inbox,
  LockKeyhole,
  MessageSquarePlus,
  ShieldCheck,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { PanelHeader } from "../common/PanelHeader";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import { resolveGroupAvailability } from "../governance/groupAvailability";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";

const supportedChannelTypes = [
  { id: "feishu", labelKey: "channelImport.sources.feishu" },
  { id: "wechat", labelKey: "channelImport.sources.wechat" },
  { id: "dingtalk", labelKey: "channelImport.sources.dingtalk" },
  { id: "slack", labelKey: "channelImport.sources.slack" },
];

export function ChannelImportPanel() {
  const { t } = useTranslation();
  const publicProjectionAvailability = resolveGroupAvailability({
    backed: false,
  });
  const runtimeNoticeAvailability = resolveGroupAvailability({
    backed: true,
    enabled: true,
  });
  const lifecycleAvailability = resolveGroupAvailability({
    backed: false,
  });

  return (
    <div
      data-phase1c-surface="channel-import"
      data-channel-workbench-shell
      data-frontend-governance-state="degraded"
      className="flex h-full min-h-0 flex-col bg-[var(--theme-bg)] text-slate-950 dark:bg-stone-950 dark:text-stone-100"
    >
      <PanelHeader
        title={t("channelImport.title")}
        subtitle={t("channelImport.subtitle")}
        icon={
          <MessageSquarePlus
            size={20}
            className="text-theme-text-secondary"
          />
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <section className="grid gap-3 lg:grid-cols-3">
          <div className={workbenchSurface.compactPanel}>
            <div className="flex items-start justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={16} className="text-stone-500" />
                  <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                    {t("channelImport.capabilities.publicSources.title")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t("channelImport.capabilities.publicSources.description")}
                </p>
              </div>
              <GovernanceAvailabilityBadge
                state={publicProjectionAvailability.state}
                labelKey={publicProjectionAvailability.labelKey}
              />
            </div>
          </div>
          <div className={workbenchSurface.compactPanel}>
            <div className="flex items-start justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <BellRing size={16} className="text-stone-500" />
                  <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                    {t("channelImport.capabilities.runtimeNotices.title")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t("channelImport.capabilities.runtimeNotices.description")}
                </p>
              </div>
              <GovernanceAvailabilityBadge
                state={runtimeNoticeAvailability.state}
                labelKey={runtimeNoticeAvailability.labelKey}
              />
            </div>
          </div>
          <div
            data-fail-closed-surface="channel-lifecycle-governance"
            className={workbenchSurface.compactPanel}
          >
            <div className="flex items-start justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <LockKeyhole size={16} className="text-stone-500" />
                  <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                    {t("channelImport.capabilities.lifecycle.title")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t("channelImport.capabilities.lifecycle.description")}
                </p>
              </div>
              <GovernanceAvailabilityBadge
                state={lifecycleAvailability.state}
                labelKey={lifecycleAvailability.labelKey}
              />
            </div>
          </div>
        </section>

        <section className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1fr)_22rem]">
          <div className={workbenchSurface.panel}>
            <div className="border-b border-[var(--theme-border)] px-4 py-3 dark:border-stone-800">
              <div className="flex items-center gap-2">
                <Cable size={16} className="text-stone-500" />
                <h2 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                  {t("channelImport.sourcesTitle")}
                </h2>
              </div>
              <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                {t("channelImport.sourcesDescription")}
              </p>
            </div>

            <div className="grid gap-3 p-4 md:grid-cols-2">
              {supportedChannelTypes.map((source) => (
                <article
                  key={source.id}
                  className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] p-3 dark:border-stone-800 dark:bg-stone-950/50"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <Inbox size={15} className="text-stone-500" />
                        <h3 className="truncate text-sm font-semibold text-stone-900 dark:text-stone-100">
                          {t(source.labelKey)}
                        </h3>
                      </div>
                      <p className="mt-2 text-xs leading-5 text-stone-500 dark:text-stone-400">
                        {t("channelImport.sourceFailClosedDescription")}
                      </p>
                    </div>
                    <GovernanceAvailabilityBadge
                      state="unavailable"
                      labelKey="governance.unavailable"
                    />
                  </div>
                </article>
              ))}
            </div>
          </div>

          <div data-channel-projection-gap className={workbenchSurface.panel}>
            <WorkbenchStateSurface
              className="h-full max-w-none border-0 shadow-none"
              state="degraded"
              surface="channel-import-projection"
              title={t("channelImport.backendGap.title")}
              description={t("channelImport.backendGap.description")}
              details={[
                t("channelImport.backendGap.publicProjection"),
                t("channelImport.backendGap.adminProjection"),
                t("channelImport.backendGap.auditProjection"),
              ]}
            />
          </div>
        </section>
      </div>
    </div>
  );
}
