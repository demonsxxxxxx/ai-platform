import { useEffect, useMemo, useState } from "react";
import { Bot, CheckCircle2, LockKeyhole, Search, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import { PanelHeader } from "../common/PanelHeader";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import { isPermissionError } from "../governance/frontendGovernanceState";
import { resolveGroupAvailability } from "../governance/groupAvailability";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { agentApi } from "../../services/api/agent";
import type { AgentInfo, AgentListResponse } from "../../types";

type DirectoryLoadState = {
  agents: AgentInfo[];
  count: number;
  defaultAgent?: string | null;
  allowedModelIds?: string[] | null;
};

function normalizeQuery(value: string) {
  return value.trim().toLowerCase();
}

function agentMatchesQuery(agent: AgentInfo, query: string) {
  if (!query) return true;
  return [agent.id, agent.name, agent.description, agent.version]
    .filter(Boolean)
    .some((value) => value!.toLowerCase().includes(query));
}

function normalizeAgentList(response: AgentListResponse | null): DirectoryLoadState {
  const agents = response?.agents ?? [];
  return {
    agents,
    count: response?.count ?? agents.length,
    defaultAgent: response?.default_agent ?? null,
    allowedModelIds: response?.allowed_model_ids ?? null,
  };
}

/** Render the governed read-only Agent directory from the public /api/agents projection. */
export function AgentDirectoryPanel() {
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState("");
  const [directory, setDirectory] = useState<DirectoryLoadState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function loadAgents() {
      setIsLoading(true);
      setLoadError(null);
      try {
        const response = await agentApi.list();
        if (!cancelled) {
          setDirectory(normalizeAgentList(response));
        }
      } catch (err) {
        if (!cancelled) {
          setDirectory(null);
          setLoadError(
            err instanceof Error
              ? err.message
              : t("agentDirectory.loadFailed", "Agent directory is unavailable"),
          );
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    void loadAgents();

    return () => {
      cancelled = true;
    };
  }, [t]);

  const query = normalizeQuery(searchQuery);
  const filteredAgents = useMemo(
    () => (directory?.agents ?? []).filter((agent) => agentMatchesQuery(agent, query)),
    [directory?.agents, query],
  );
  const readyState = directory && directory.agents.length > 0;
  const publicAvailability = resolveGroupAvailability({
    backed: true,
    enabled: Boolean(readyState),
  });
  const adminAvailability = resolveGroupAvailability({
    backed: false,
    enabled: false,
  });

  if (isLoading) {
    return (
      <div
        data-agent-directory-shell
        data-frontend-governance-state="loading"
        className="flex h-full min-h-0 items-center justify-center bg-[var(--theme-bg)] px-4"
      >
        <WorkbenchStateSurface
          state="loading"
          surface="agent-public-directory"
          title={t("agentDirectory.loading", "Loading Agent directory")}
          description={t(
            "agentDirectory.loadingDescription",
            "Checking the public Agent projection for this workspace.",
          )}
        />
      </div>
    );
  }

  if (loadError) {
    const forbidden = isPermissionError(loadError);
    return (
      <div
        data-agent-directory-shell
        data-frontend-governance-state={forbidden ? "forbidden" : "degraded"}
        className="flex h-full min-h-0 items-center justify-center bg-[var(--theme-bg)] px-4"
      >
        <WorkbenchStateSurface
          state={forbidden ? "forbidden" : "degraded"}
          surface="agent-public-directory"
          title={
            forbidden
              ? t("agentDirectory.forbidden", "Agent directory is governed")
              : t("agentDirectory.unavailable", "Agent directory is unavailable")
          }
          description={
            forbidden
              ? t(
                  "agentDirectory.forbiddenDescription",
                  "This account cannot read the public Agent projection yet.",
                )
              : t(
                  "agentDirectory.unavailableDescription",
                  "The public Agent projection did not return data. Chat can keep using the current default Agent while admin controls stay locked.",
                )
          }
          details={[loadError]}
        />
      </div>
    );
  }

  return (
    <div
      data-agent-directory-shell
      data-frontend-governance-state={readyState ? "ready" : "degraded"}
      className="flex h-full min-h-0 flex-col bg-[var(--theme-bg)] text-slate-950 dark:bg-stone-950 dark:text-stone-100"
    >
      <PanelHeader
        title={t("agentDirectory.title", "Agents")}
        subtitle={t(
          "agentDirectory.subtitle",
          "Browse approved Agents from the public projection. Configuration writes stay governed.",
        )}
        icon={<Bot size={20} className="text-theme-text-secondary" />}
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder={t("agentDirectory.searchPlaceholder", "Search Agents")}
      />

      <div className="px-4 pb-2 pt-3">
        <section className="grid gap-3 lg:grid-cols-3">
          <div className={workbenchSurface.compactPanel}>
            <div className="flex items-start justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={16} className="text-stone-500" />
                  <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                    {t("agentDirectory.publicProjection", "Public projection")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t(
                    "agentDirectory.publicProjectionDescription",
                    "This page reads /api/agents only and does not expose prompts, tools, role assignments, or legacy config APIs.",
                  )}
                </p>
              </div>
              <GovernanceAvailabilityBadge
                state={publicAvailability.state}
                labelKey={publicAvailability.labelKey}
              />
            </div>
          </div>
          <div className={workbenchSurface.compactPanel}>
            <div className="flex items-start justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-stone-500" />
                  <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                    {t("agentDirectory.directoryStats", "Directory status")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t(
                    "agentDirectory.directoryStatsDescription",
                    "{{count}} Agents published. Default: {{defaultAgent}}. Model scope: {{models}}.",
                    {
                      count: directory?.count ?? 0,
                      defaultAgent:
                        directory?.defaultAgent ?? t("workbench.none", "None"),
                      models:
                        directory?.allowedModelIds?.length ??
                        t("agentDirectory.modelScopeAll", "all approved models"),
                    },
                  )}
                </p>
              </div>
            </div>
          </div>
          <div
            data-fail-closed-surface="agent-admin-governance"
            className={workbenchSurface.compactPanel}
          >
            <div className="flex items-start justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <LockKeyhole size={16} className="text-stone-500" />
                  <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                    {t("agentDirectory.adminGovernance", "Admin configuration")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t(
                    "agentDirectory.adminGovernanceDescription",
                    "Role assignment, prompt editing, model binding, and tool governance need backend admin projections before they open here.",
                  )}
                </p>
              </div>
              <GovernanceAvailabilityBadge
                state={adminAvailability.state}
                labelKey={adminAvailability.labelKey}
              />
            </div>
          </div>
        </section>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {filteredAgents.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-theme-text-secondary">
            {query ? (
              <Search size={42} className="mb-3 text-theme-text-secondary" />
            ) : (
              <Bot size={42} className="mb-3 text-theme-text-secondary" />
            )}
            <p className="text-center text-sm">
              {query
                ? t("agentDirectory.noMatchingAgents", "No matching Agents")
                : t("agentDirectory.noAgents", "No Agents are published")}
            </p>
            <p className="mt-2 max-w-md text-center text-xs leading-5 text-stone-500 dark:text-stone-400">
              {t(
                "agentDirectory.emptyDescription",
                "The directory only shows public Agents returned by the backend projection. Admin configuration remains closed.",
              )}
            </p>
          </div>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
            {filteredAgents.map((agent) => {
              const isDefault = agent.id === directory?.defaultAgent;
              const optionCount = Object.keys(agent.options ?? {}).length;
              return (
                <article
                  key={agent.id}
                  className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-4 shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-start gap-3">
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] text-slate-600 ring-1 ring-[var(--theme-border)] dark:bg-stone-950 dark:text-stone-300 dark:ring-stone-800">
                        <Bot size={19} />
                      </div>
                      <div className="min-w-0">
                        <h3 className="truncate text-sm font-semibold text-stone-900 dark:text-stone-100">
                          {agent.name}
                        </h3>
                        <p className="mt-1 truncate text-xs text-stone-500 dark:text-stone-400">
                          {agent.id}
                        </p>
                      </div>
                    </div>
                    {isDefault ? (
                      <span className="shrink-0 rounded-md bg-emerald-50 px-2 py-1 text-[11px] font-semibold text-emerald-700 dark:bg-emerald-900/25 dark:text-emerald-200">
                        {t("workbench.defaultAgent", "Default")}
                      </span>
                    ) : null}
                  </div>

                  <p className="mt-3 line-clamp-2 text-xs leading-5 text-stone-500 dark:text-stone-400">
                    {agent.description ||
                      t("agentDirectory.noDescription", "No description published")}
                  </p>

                  <dl className="mt-4 grid grid-cols-2 gap-2 text-xs">
                    <div className="rounded-md bg-[var(--theme-bg-sidebar)] p-2 dark:bg-stone-950/50">
                      <dt className="text-stone-400 dark:text-stone-500">
                        {t("agentDirectory.version", "Version")}
                      </dt>
                      <dd className="mt-1 truncate font-medium text-stone-700 dark:text-stone-200">
                        {agent.version || t("workbench.none", "None")}
                      </dd>
                    </div>
                    <div className="rounded-md bg-[var(--theme-bg-sidebar)] p-2 dark:bg-stone-950/50">
                      <dt className="text-stone-400 dark:text-stone-500">
                        {t("agentDirectory.options", "Options")}
                      </dt>
                      <dd className="mt-1 truncate font-medium text-stone-700 dark:text-stone-200">
                        {t("agentDirectory.optionCount", "{{count}} published", {
                          count: optionCount,
                        })}
                      </dd>
                    </div>
                  </dl>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

export default AgentDirectoryPanel;
