import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BellRing,
  Cable,
  CheckCircle2,
  Inbox,
  LockKeyhole,
  MessageSquarePlus,
  RotateCw,
  ShieldCheck,
  TestTube2,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { PanelHeader } from "../common/PanelHeader";
import { PanelLoadingState } from "../common/PanelLoadingState";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import { resolveGroupAvailability } from "../governance/groupAvailability";
import {
  buildFrontendGovernanceSmokeAttributes,
  isPermissionError,
  resolveFrontendGovernanceState,
} from "../governance/frontendGovernanceState";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { channelApi } from "../../services/api/channel";
import { useAuth } from "../../hooks/useAuth";
import { useChannelAdminOperations } from "../../hooks/useChannelAdminOperations";
import { Permission, type PublicChannelResponse } from "../../types";

function formatCapability(capability: string): string {
  return capability.replace(/[:_]/g, " ");
}

function formatConnectionState(connectionState: string): string {
  return connectionState.replace(/[:_]/g, " ");
}

export function ChannelImportPanel() {
  const { t } = useTranslation();
  const { hasPermission, isAuthenticated, isLoading: authLoading } = useAuth();
  const canAdminChannels = hasPermission(Permission.CHANNEL_ADMIN);
  const channelAdminOperations = useChannelAdminOperations({
    enabled: canAdminChannels,
  });
  const [channels, setChannels] = useState<PublicChannelResponse[]>([]);
  const [workspaceId, setWorkspaceId] = useState("default");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [testingChannelId, setTestingChannelId] = useState<string | null>(null);
  const permissionDenied = isPermissionError(error);
  const governanceState = resolveFrontendGovernanceState({
    isAuthenticated,
    isLoading: authLoading || isLoading,
    hasWorkspace: Boolean(workspaceId),
    hasPermission: !permissionDenied,
    featureEnabled: true,
    projectionError: error,
  });

  const fetchCatalog = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await channelApi.listCatalog(workspaceId);
      setChannels(response.channels);
      setWorkspaceId(response.workspace_id || "default");
    } catch (err) {
      setChannels([]);
      setError(err instanceof Error ? err.message : "channel_catalog_failed");
    } finally {
      setIsLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    fetchCatalog();
  }, [fetchCatalog]);

  const enabledCount = useMemo(
    () => channels.filter((channel) => channel.enabled).length,
    [channels],
  );
  const publicProjectionAvailability = resolveGroupAvailability({
    backed: !error,
    enabled: governanceState === "ready",
  });
  const runtimeNoticeAvailability = resolveGroupAvailability({
    backed: true,
    enabled: true,
  });
  const lifecycleAvailability = resolveGroupAvailability({
    backed: true,
    enabled: canAdminChannels,
    adminOnly: !canAdminChannels,
  });

  const handleAdminTest = async (channelId: string) => {
    setTestingChannelId(channelId);
    try {
      const result = await channelAdminOperations.testAdminChannel(
        channelId,
        workspaceId,
      );
      toast.success(result.message || t("channelImport.adminTestQueued"));
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : t("channelImport.adminTestFailed"),
      );
    } finally {
      setTestingChannelId(null);
    }
  };

  return (
    <div
      data-phase1c-surface="channel-import"
      data-channel-workbench-shell
      {...buildFrontendGovernanceSmokeAttributes(governanceState)}
      className={workbenchSurface.page}
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
        actions={
          <button
            type="button"
            onClick={fetchCatalog}
            disabled={isLoading}
            className="btn-secondary h-10"
            title={t("common.refresh")}
          >
            <RotateCw
              size={16}
              className={isLoading ? "animate-spin" : undefined}
            />
            <span className="hidden sm:inline">{t("common.refresh")}</span>
          </button>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <section className="grid gap-3 lg:grid-cols-3">
          <div className={workbenchSurface.compactPanel}>
            <div className="flex items-start justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={16} className="text-[var(--theme-text-secondary)]" />
                  <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                    {t("channelImport.capabilities.publicSources.title")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
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
                  <BellRing size={16} className="text-[var(--theme-text-secondary)]" />
                  <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                    {t("channelImport.capabilities.runtimeNotices.title")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
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
            data-channel-admin-governance
            data-fail-closed-surface="channel-admin-governance"
            className={workbenchSurface.compactPanel}
          >
            <div className="flex items-start justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <LockKeyhole size={16} className="text-[var(--theme-text-secondary)]" />
                  <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                    {t("channelImport.capabilities.lifecycle.title")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
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
            <div className="border-b border-[var(--theme-border)] px-4 py-3">
              <div className="flex items-center gap-2">
                <Cable size={16} className="text-[var(--theme-text-secondary)]" />
                <h2 className="text-sm font-semibold text-[var(--theme-text)]">
                  {t("channelImport.sourcesTitle")}
                </h2>
              </div>
              <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
                {t("channelImport.sourcesDescription")}
              </p>
            </div>

            {isLoading ? (
              <PanelLoadingState
                text={t("channelImport.loading")}
                containerClassName="min-h-72"
              />
            ) : error ? (
              <div className="p-4">
                <WorkbenchStateSurface
                  className="max-w-none border-0 shadow-none"
                  state={governanceState}
                  surface="channel-catalog-projection"
                  title={
                    permissionDenied
                      ? t("workbench.states.forbidden.title")
                      : t("channelImport.unavailable.title")
                  }
                  description={
                    permissionDenied
                      ? t("workbench.states.forbidden.description")
                      : t("channelImport.unavailable.description")
                  }
                  details={[error]}
                />
              </div>
            ) : channels.length === 0 ? (
              <div className="p-4">
                <WorkbenchStateSurface
                  className="max-w-none border-0 shadow-none"
                  state="degraded"
                  surface="channel-catalog-empty"
                  title={t("channelImport.empty.title")}
                  description={t("channelImport.empty.description")}
                />
              </div>
            ) : (
              <div
                data-channel-catalog-list
                className={`${workbenchSurface.catalog.cardGrid} p-4`}
              >
                {channels.map((channel) => (
                  <article
                    key={channel.channel_id}
                    className={workbenchSurface.catalog.entryCard}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <Inbox size={15} className="text-[var(--theme-text-secondary)]" />
                          <h3 className="min-w-0 text-sm font-semibold leading-5 text-[var(--theme-text)]">
                            {channel.display_name}
                          </h3>
                        </div>
                        <p className="mt-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
                          {t("channelImport.catalogItem.description", {
                            type: channel.channel_type,
                            workspace: channel.workspace_id,
                          })}
                        </p>
                      </div>
                      <GovernanceAvailabilityBadge
                        state={channel.enabled ? "enabled" : "disabled"}
                        labelKey={
                          channel.enabled
                            ? "governance.enabled"
                            : "governance.disabled"
                        }
                      />
                    </div>

                    <div className="mt-3 grid gap-2 text-xs text-[var(--theme-text-secondary)]">
                      <div
                        data-channel-connection-state={channel.connection_state}
                        className="flex items-center justify-between gap-3 rounded-md bg-[var(--theme-bg-card)] px-2.5 py-2 ring-1 ring-[var(--theme-border)]"
                      >
                        <span>{t("channelImport.connection.label")}</span>
                        <span className="font-medium text-[var(--theme-text)]">
                          {t(
                            `channelImport.connection.states.${channel.connection_state}`,
                            {
                              defaultValue: formatConnectionState(
                                channel.connection_state,
                              ),
                            },
                          )}
                        </span>
                      </div>
                      <div className="flex items-center justify-between gap-3 rounded-md bg-[var(--theme-bg-card)] px-2.5 py-2 ring-1 ring-[var(--theme-border)]">
                        <span>{t("channelImport.redaction")}</span>
                        <span className="font-medium text-[var(--theme-text)]">
                          {channel.redaction_policy}
                        </span>
                      </div>
                      <div className="flex items-center justify-between gap-3 rounded-md bg-[var(--theme-bg-card)] px-2.5 py-2 ring-1 ring-[var(--theme-border)]">
                        <span>{t("channelImport.retention")}</span>
                        <span className="font-medium text-[var(--theme-text)]">
                          {channel.retention_policy}
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {channel.capabilities.map((capability) => (
                          <span
                            key={capability}
                            className="rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-2 py-1 text-[11px] font-medium text-[var(--theme-text-secondary)]"
                          >
                            {formatCapability(capability)}
                          </span>
                        ))}
                      </div>
                    </div>

                    {canAdminChannels ? (
                      <div className="mt-3 flex justify-end">
                        <button
                          type="button"
                          onClick={() => handleAdminTest(channel.channel_id)}
                          disabled={testingChannelId === channel.channel_id}
                          aria-busy={testingChannelId === channel.channel_id}
                          className="btn-secondary h-9"
                        >
                          <TestTube2 size={15} />
                          <span>
                            {testingChannelId === channel.channel_id
                              ? t("channelImport.testDryRunPending")
                              : t("channelImport.testDryRun")}
                          </span>
                        </button>
                      </div>
                    ) : null}
                  </article>
                ))}
              </div>
            )}
          </div>

          <div className={workbenchSurface.panel}>
            <WorkbenchStateSurface
              className="h-full max-w-none border-0 shadow-none"
              state={governanceState === "loading" ? "loading" : "ready"}
              surface="channel-catalog-summary"
              icon={CheckCircle2}
              title={t("channelImport.catalogReady.title")}
              description={t("channelImport.catalogReady.description", {
                count: channels.length,
                enabled: enabledCount,
                workspace: workspaceId,
              })}
              capabilities={[
                {
                  title: t("channelImport.catalogReady.publicProjection"),
                  description: t("channelImport.catalogReady.publicProjectionDescription"),
                  state: publicProjectionAvailability.state,
                  labelKey: publicProjectionAvailability.labelKey,
                },
                {
                  title: t("channelImport.catalogReady.adminProjection"),
                  description: t("channelImport.catalogReady.adminProjectionDescription"),
                  state: lifecycleAvailability.state,
                  labelKey: lifecycleAvailability.labelKey,
                },
              ]}
            />
          </div>
        </section>
      </div>
    </div>
  );
}
