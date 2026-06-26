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
    adminOnly: true,
  });

  if (isLoading) {
    return (
      <div
        data-agent-directory-shell
        data-frontend-governance-state="loading"
        className={workbenchSurface.statePage}
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
        className={workbenchSurface.statePage}
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
      className={workbenchSurface.page}
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

      <div className={workbenchSurface.catalog.summaryGrid}>
        <section className={`${workbenchSurface.catalog.summaryCard} flex items-start justify-between gap-3`}>
          <div className="flex min-w-0 items-start gap-3">
            <div className={workbenchSurface.catalog.compactIconBox}>
              <ShieldCheck size={16} />
            </div>
            <div className="min-w-0">
              <h3 className={workbenchSurface.catalog.title}>
                {t("agentDirectory.publicProjection", "Public projection")}
              </h3>
              <p className={`mt-1 ${workbenchSurface.catalog.body}`}>
                {t(
                  "agentDirectory.publicProjectionDescription",
                  "This page reads /api/agents only and does not expose prompts, tools, role assignments, or legacy config APIs.",
                )}
              </p>
            </div>
          </div>
          <GovernanceAvailabilityBadge
            state={publicAvailability.state}
            labelKey={publicAvailability.labelKey}
          />
        </section>
        <section className={`${workbenchSurface.catalog.summaryCard} flex items-start justify-between gap-3`}>
          <div className="flex min-w-0 items-start gap-3">
            <div className={workbenchSurface.catalog.compactIconBox}>
              <CheckCircle2 size={16} />
            </div>
            <div className="min-w-0">
              <h3 className={workbenchSurface.catalog.title}>
                {t("agentDirectory.directoryStats", "Directory status")}
              </h3>
              <p className={`mt-1 ${workbenchSurface.catalog.body}`}>
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
        </section>
        <section
          data-fail-closed-surface="agent-admin-governance"
          className={`${workbenchSurface.catalog.summaryCard} flex items-start justify-between gap-3`}
        >
          <div className="flex min-w-0 items-start gap-3">
            <div className={workbenchSurface.catalog.compactIconBox}>
              <LockKeyhole size={16} />
            </div>
            <div className="min-w-0">
              <h3 className={workbenchSurface.catalog.title}>
                {t("agentDirectory.adminGovernance", "Admin configuration")}
              </h3>
              <p className={`mt-1 ${workbenchSurface.catalog.body}`}>
                {t(
                  "agentDirectory.adminGovernanceDescription",
                  "Role assignment, prompt editing, model binding, and tool governance remain locked to administrator-controlled surfaces.",
                )}
              </p>
            </div>
          </div>
          <GovernanceAvailabilityBadge
            state={adminAvailability.state}
            labelKey={adminAvailability.labelKey}
          />
        </section>
      </div>

      <div className={workbenchSurface.catalog.content}>
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
            <p className={`mt-2 max-w-md text-center ${workbenchSurface.catalog.body}`}>
              {t(
                "agentDirectory.emptyDescription",
                "The directory only shows public Agents returned by the backend projection. Admin configuration remains closed.",
              )}
            </p>
          </div>
        ) : (
          <div className={workbenchSurface.catalog.cardGrid}>
            {filteredAgents.map((agent) => {
              const isDefault = agent.id === directory?.defaultAgent;
              const optionCount = Object.keys(agent.options ?? {}).length;
              return (
                <article
                  key={agent.id}
                  className={workbenchSurface.catalog.entryCard}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-start gap-3">
                      <div className={workbenchSurface.catalog.iconBox}>
                        <Bot size={19} />
                      </div>
                      <div className="min-w-0">
                        <h3 className={`truncate ${workbenchSurface.catalog.title}`}>
                          {agent.name}
                        </h3>
                        <p className={`mt-1 truncate text-xs ${workbenchSurface.catalog.muted}`}>
                          {agent.id}
                        </p>
                      </div>
                    </div>
                    {isDefault ? (
                      <span className="shrink-0 rounded-md bg-[var(--theme-success-soft)] px-2 py-1 text-[11px] font-semibold text-[var(--theme-success)] ring-1 ring-[var(--theme-success-ring)]">
                        {t("workbench.defaultAgent", "Default")}
                      </span>
                    ) : null}
                  </div>

                  <p className={`mt-3 line-clamp-2 ${workbenchSurface.catalog.body}`}>
                    {agent.description ||
                      t("agentDirectory.noDescription", "No description published")}
                  </p>

                  <dl className="mt-4 grid grid-cols-2 gap-2 text-xs">
                    <div className={workbenchSurface.catalog.metricTile}>
                      <dt className={workbenchSurface.catalog.label}>
                        {t("agentDirectory.version", "Version")}
                      </dt>
                      <dd className="mt-1 truncate font-medium text-[var(--theme-text)]">
                        {agent.version || t("workbench.none", "None")}
                      </dd>
                    </div>
                    <div className={workbenchSurface.catalog.metricTile}>
                      <dt className={workbenchSurface.catalog.label}>
                        {t("agentDirectory.options", "Options")}
                      </dt>
                      <dd className="mt-1 truncate font-medium text-[var(--theme-text)]">
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
