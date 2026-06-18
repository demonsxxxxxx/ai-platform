import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  Bell,
  Bot,
  Boxes,
  BrainCircuit,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { PanelHeader } from "../common/PanelHeader";
import {
  phase1ProjectionApi,
  type ActiveNotificationProjection,
  type AdminSkillSyncProjection,
  type AdminSkillVersionProjection,
  type AdminToolPolicyProjection,
  type AgentAppProjection,
  type Phase1ModelCatalogProjection,
} from "../../services/api/phase1Projection";
import type { AgentInfo } from "../../types";

type Loadable<T> =
  | { status: "loading"; data?: undefined; error?: undefined }
  | { status: "loaded"; data: T; error?: undefined }
  | { status: "error"; data?: undefined; error: string };

function useProjection<T>(load: () => Promise<T>): Loadable<T> {
  const [state, setState] = useState<Loadable<T>>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    load()
      .then((data) => {
        if (!cancelled) setState({ status: "loaded", data });
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({
            status: "error",
            error: error instanceof Error ? error.message : String(error),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  return state;
}

function useAsyncProjection<T>(load: () => Promise<T>): {
  state: Loadable<T>;
  reload: () => Promise<void>;
} {
  const [state, setState] = useState<Loadable<T>>({ status: "loading" });

  const reload = useCallback(async () => {
    setState({ status: "loading" });
    try {
      const data = await load();
      setState({ status: "loaded", data });
    } catch (error: unknown) {
      setState({
        status: "error",
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }, [load]);

  useEffect(() => {
    reload();
  }, [reload]);

  return { state, reload };
}

function PanelShell({
  title,
  subtitle,
  icon,
  actions,
  children,
}: {
  title: string;
  subtitle: string;
  icon: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="glass-shell flex h-full min-h-0 flex-col">
      <PanelHeader
        title={title}
        subtitle={subtitle}
        icon={icon}
        actions={actions}
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {children}
      </div>
    </div>
  );
}

function StateBlock<T>({
  state,
  empty,
  children,
}: {
  state: Loadable<T>;
  empty: (data: T) => boolean;
  children: (data: T) => React.ReactNode;
}) {
  if (state.status === "loading") {
    return (
      <div className="rounded-lg border border-stone-200 bg-white p-4 text-sm text-stone-500 shadow-sm dark:border-stone-800 dark:bg-stone-900 dark:text-stone-400">
        Loading projection...
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
        {state.error}
      </div>
    );
  }
  if (empty(state.data)) {
    return (
      <div className="rounded-lg border border-dashed border-stone-300 bg-white p-4 text-sm text-stone-500 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-400">
        No records in the current projection.
      </div>
    );
  }
  return <>{children(state.data)}</>;
}

function ProjectionCard({
  title,
  meta,
  description,
  status,
}: {
  title: string;
  meta?: string;
  description?: string;
  status?: string;
}) {
  return (
    <article className="rounded-lg border border-stone-200 bg-white p-4 shadow-sm dark:border-stone-800 dark:bg-stone-900">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-stone-900 dark:text-stone-50">
            {title}
          </h3>
          {meta && (
            <p className="mt-1 text-xs text-stone-500 dark:text-stone-400">
              {meta}
            </p>
          )}
        </div>
        {status && (
          <span className="shrink-0 rounded-md bg-stone-100 px-2 py-1 text-xs font-medium text-stone-600 dark:bg-stone-800 dark:text-stone-300">
            {status}
          </span>
        )}
      </div>
      {description && (
        <p className="mt-3 line-clamp-3 text-sm leading-6 text-stone-600 dark:text-stone-300">
          {description}
        </p>
      )}
    </article>
  );
}

function InlineWarning({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
      <p className="font-medium">{title}</p>
      <div className="mt-1 leading-6">{children}</div>
    </div>
  );
}

export function Phase1SkillsGovernancePanel() {
  const [syncState, setSyncState] = useState<Loadable<AdminSkillSyncProjection>>(
    { status: "loaded", data: { synced: [] } },
  );
  const loadSkillsProjection = useCallback(
    () => phase1ProjectionApi.listSkillGovernanceProjection(),
    [],
  );
  const { state, reload } = useAsyncProjection(loadSkillsProjection);

  const handleSyncBuiltinSkills = async () => {
    setSyncState({ status: "loading" });
    try {
      const sync = await phase1ProjectionApi.syncBuiltinSkills();
      setSyncState({ status: "loaded", data: sync });
      await reload();
    } catch (error: unknown) {
      setSyncState({
        status: "error",
        error: error instanceof Error ? error.message : String(error),
      });
    }
  };

  return (
    <PanelShell
      title="Skills Governance"
      subtitle="Phase 1 read projection backed by public capabilities; sync is explicit admin action"
      icon={<Sparkles size={20} />}
      actions={
        <button
          type="button"
          onClick={handleSyncBuiltinSkills}
          disabled={syncState.status === "loading"}
          className="inline-flex h-9 items-center gap-2 rounded-lg border border-stone-200 bg-white px-3 text-sm font-medium text-stone-700 shadow-sm transition-colors hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-stone-800 dark:bg-stone-900 dark:text-stone-200 dark:hover:bg-stone-800"
          title="Sync built-in governed skills"
        >
          {syncState.status === "loading" ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <RefreshCw size={14} />
          )}
          <span>Sync built-ins</span>
        </button>
      }
    >
      {syncState.status === "loaded" && syncState.data.synced.length > 0 && (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-300">
          Synced {syncState.data.synced.length} built-in skill versions.
        </div>
      )}
      {syncState.status === "error" && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
          {syncState.error}
        </div>
      )}
      <StateBlock
        state={state}
        empty={(data) =>
          data.publicAgents.length === 0 &&
          data.agentApps.length === 0 &&
          data.details.length === 0
        }
      >
        {(data) => (
          <div className="space-y-4">
            {data.agentAppsError && (
              <InlineWarning title="Agent app projection is unavailable">
                {data.agentAppsError}
              </InlineWarning>
            )}
            {data.detailErrors.length > 0 && (
              <InlineWarning title="Some governed Skill details could not be loaded">
                <ul className="list-disc space-y-1 pl-5">
                  {data.detailErrors.map((item) => (
                    <li key={item.skill_id}>
                      <span className="font-medium">{item.skill_id}</span>:{" "}
                      {item.error}
                    </li>
                  ))}
                </ul>
              </InlineWarning>
            )}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <ProjectionCard
                title="Public capabilities"
                meta={`${data.publicAgents.length} agents`}
                status="read"
              />
              <ProjectionCard
                title="Agent apps"
                meta={`${data.agentApps.length} apps`}
                status="admin"
              />
              <ProjectionCard
                title="Governed skills"
                meta={`${data.details.length} skills`}
                status="read"
              />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-1">
              <ProjectionCard
                title="Source authority"
                meta="/api/agents + /api/ai/agent-apps + /api/ai/admin/skills/{id}"
                status="current"
              />
            </div>
            {data.publicAgents.length > 0 && (
              <section>
                <h2 className="mb-3 text-sm font-semibold text-stone-800 dark:text-stone-100">
                  Runtime capabilities
                </h2>
                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                  {data.publicAgents.map((agent: AgentInfo) => (
                    <ProjectionCard
                      key={agent.id}
                      title={agent.name}
                      meta={agent.id}
                      description={agent.description}
                      status={agent.supports_sandbox ? "sandbox" : "chat"}
                    />
                  ))}
                </div>
              </section>
            )}
            {data.agentApps.length > 0 && (
              <section>
                <h2 className="mb-3 text-sm font-semibold text-stone-800 dark:text-stone-100">
                  Admin agent apps
                </h2>
                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                  {data.agentApps.map((app) => (
                    <ProjectionCard
                      key={app.app_id}
                      title={app.name}
                      meta={`${app.app_id} · ${app.mode}`}
                      description={`Default skill: ${app.default_skill_id}`}
                      status={app.status}
                    />
                  ))}
                </div>
              </section>
            )}
            {data.details.length > 0 && (
              <section>
                <h2 className="mb-3 text-sm font-semibold text-stone-800 dark:text-stone-100">
                  Governed Skill details
                </h2>
                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                  {data.details.map((skill) => (
                    <ProjectionCard
                      key={skill.skill_id}
                      title={skill.skill_id}
                      meta={`${skill.versions.length} versions`}
                      description={
                        skill.description ||
                        latestSkillDescription(skill.versions)
                      }
                      status={skill.status}
                    />
                  ))}
                </div>
              </section>
            )}
          </div>
        )}
      </StateBlock>
    </PanelShell>
  );
}

function latestSkillDescription(
  versions: AdminSkillVersionProjection[],
): string {
  const latest = versions[0];
  return latest?.description || latest?.content_hash || "No description";
}

export function Phase1ToolPolicyPanel() {
  const loadToolPolicies = useCallback(
    () => phase1ProjectionApi.listToolPolicies(),
    [],
  );
  const state = useProjection(loadToolPolicies);

  return (
    <PanelShell
      title="Tool Policies"
      subtitle="Current admin tool policy inventory and decision requirements"
      icon={<ShieldCheck size={20} />}
    >
      <StateBlock
        state={state}
        empty={(data) => data.tool_policies.length === 0}
      >
        {(data) => (
          <div className="space-y-4">
            <ProjectionCard
              title="Contract"
              meta={data.contract_version}
              description={`${data.summary.returned_count} policies returned for tenant ${data.tenant_id}.`}
              status="admin"
            />
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              {data.tool_policies.map((policy: AdminToolPolicyProjection) => (
                <ProjectionCard
                  key={policy.tool_id}
                  title={policy.name || policy.tool_id}
                  meta={`${policy.server_id || "registry"} · ${policy.risk_level}`}
                  description={policy.description || policy.reason || ""}
                  status={
                    policy.requires_decision
                      ? "requires decision"
                      : policy.effective_status
                  }
                />
              ))}
            </div>
          </div>
        )}
      </StateBlock>
    </PanelShell>
  );
}

export function Phase1AgentAppsPanel() {
  const loadAgentAppsProjection = useCallback(async () => {
    const [agentApps, publicAgents] = await Promise.all([
      phase1ProjectionApi.listAgentApps(),
      phase1ProjectionApi.listPublicAgents(),
    ]);
    return { agentApps: agentApps.agent_apps, publicAgents: publicAgents.agents };
  }, []);
  const state = useProjection(loadAgentAppsProjection);

  return (
    <PanelShell
      title="Agent Apps"
      subtitle="Admin agent app projection plus ordinary-user public agent capabilities"
      icon={<Bot size={20} />}
    >
      <StateBlock
        state={state}
        empty={(data) =>
          data.agentApps.length === 0 && data.publicAgents.length === 0
        }
      >
        {(data) => (
          <div className="space-y-5">
            <section>
              <h2 className="mb-3 text-sm font-semibold text-stone-800 dark:text-stone-100">
                Admin agent apps
              </h2>
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {data.agentApps.map((app: AgentAppProjection) => (
                  <ProjectionCard
                    key={app.app_id}
                    title={app.name}
                    meta={`${app.app_id} · ${app.mode}`}
                    description={`Default skill: ${app.default_skill_id}`}
                    status={app.status}
                  />
                ))}
              </div>
            </section>
            <section>
              <h2 className="mb-3 text-sm font-semibold text-stone-800 dark:text-stone-100">
                Public chat capabilities
              </h2>
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {data.publicAgents.map((agent: AgentInfo) => (
                  <ProjectionCard
                    key={agent.id}
                    title={agent.name}
                    meta={agent.id}
                    description={agent.description}
                    status={agent.supports_sandbox ? "sandbox" : "chat"}
                  />
                ))}
              </div>
            </section>
          </div>
        )}
      </StateBlock>
    </PanelShell>
  );
}

export function Phase1ModelCatalogPanel() {
  const loadModelCatalog = useCallback(
    () => phase1ProjectionApi.listModelCatalog(),
    [],
  );
  const state = useProjection(loadModelCatalog);

  return (
    <PanelShell
      title="Model Catalog"
      subtitle="Read-only model availability and provider projection used by chat"
      icon={<BrainCircuit size={20} />}
    >
      <StateBlock state={state} empty={(data) => data.models.length === 0}>
        {(data: Phase1ModelCatalogProjection) => (
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <ProjectionCard
                title="Available models"
                meta={`${data.enabled_count}/${data.count} enabled`}
                status="public"
              />
              <ProjectionCard
                title="Providers"
                meta={`${data.providers.length} providers`}
                status="read"
              />
              <ProjectionCard
                title="Source authority"
                meta="/api/agent/models/available"
                status="current"
              />
            </div>
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              {data.models.map((model) => (
                <ProjectionCard
                  key={model.id}
                  title={model.label || model.value}
                  meta={`${model.provider || "provider"} · ${model.value}`}
                  description={model.description}
                  status={model.id}
                />
              ))}
            </div>
          </div>
        )}
      </StateBlock>
    </PanelShell>
  );
}

export function Phase1NotificationsPanel() {
  const loadActiveNotifications = useCallback(
    () => phase1ProjectionApi.listActiveNotifications(),
    [],
  );
  const state = useProjection(loadActiveNotifications);

  return (
    <PanelShell
      title="Active Notifications"
      subtitle="Read-only active notification projection; CRUD remains backend Phase 2"
      icon={<Bell size={20} />}
    >
      <StateBlock state={state} empty={(data) => data.length === 0}>
        {(data) => (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {data.map((notification: ActiveNotificationProjection, index) => (
              <ProjectionCard
                key={notification.id || String(index)}
                title={notification.title || `Notification ${index + 1}`}
                meta={notification.type || notification.level || "active"}
                description={notification.content}
                status={notification.level || "active"}
              />
            ))}
          </div>
        )}
      </StateBlock>
    </PanelShell>
  );
}

export function Phase1FilesPlaceholderPanel() {
  return (
    <PanelShell
      title="Artifacts"
      subtitle="Run artifacts remain available in chat and playback; standalone file library needs a dedicated projection"
      icon={<Boxes size={20} />}
    >
      <div className="rounded-lg border border-dashed border-stone-300 bg-white p-4 text-sm leading-6 text-stone-600 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-300">
        Authorized artifact preview and download are active inside chat and run
        playback. A tenant-scoped file-library list is still a separate backend
        contract.
      </div>
    </PanelShell>
  );
}
