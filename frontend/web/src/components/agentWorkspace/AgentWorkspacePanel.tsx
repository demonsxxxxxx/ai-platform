import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Bot,
  Brain,
  CheckCircle2,
  Clock3,
  FileText,
  ListChecks,
  RefreshCw,
  ShieldCheck,
  SquareTerminal,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { PanelHeader } from "../common/PanelHeader";
import {
  buildFrontendGovernanceSmokeAttributes,
  isPermissionError,
  resolveFrontendGovernanceState,
} from "../governance/frontendGovernanceState";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import { resolveGroupAvailability } from "../governance/groupAvailability";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { useAuth } from "../../hooks/useAuth";
import { fetchAgentWorkspace } from "../../services/api/agent";
import { formatDateTimeShort } from "../../utils/datetime";
import type {
  AgentWorkspaceArtifact,
  AgentWorkspaceConsoleEvent,
  AgentWorkspaceConsoleStep,
  AgentWorkspaceJsonValue,
  AgentWorkspaceProjection,
  AgentWorkspaceRunSummary,
  AgentWorkspaceSession,
  AgentWorkspaceToolPermission,
} from "../../types";

type Tone = "neutral" | "success" | "warning" | "danger" | "info";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function statusTone(status: string | null | undefined): Tone {
  const normalized = (status ?? "").toLowerCase();
  if (["success", "succeeded", "completed", "available", "active"].includes(normalized)) {
    return "success";
  }
  if (["running", "queued", "pending", "idle"].includes(normalized)) {
    return "info";
  }
  if (["blocked", "waiting", "warning"].includes(normalized)) {
    return "warning";
  }
  if (["failed", "error", "cancelled", "denied"].includes(normalized)) {
    return "danger";
  }
  return "neutral";
}

function toneClass(tone: Tone): string {
  if (tone === "success") {
    return "bg-[var(--theme-success-soft)] text-[var(--theme-success)] ring-[var(--theme-success-ring)]";
  }
  if (tone === "warning") {
    return "bg-[var(--theme-warning-soft)] text-[var(--theme-warning)] ring-[var(--theme-warning-ring)]";
  }
  if (tone === "danger") {
    return "bg-[var(--theme-danger-soft)] text-[var(--theme-danger)] ring-[var(--theme-danger-ring)]";
  }
  if (tone === "info") {
    return "bg-[var(--theme-info-soft)] text-[var(--theme-info)] ring-[var(--theme-info-ring)]";
  }
  return "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-[var(--theme-border)]";
}

function StatusChip({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: Tone;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-1 text-[11px] font-semibold ring-1 ${toneClass(tone)}`}
    >
      {children}
    </span>
  );
}

function FieldValue({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <dt className={workbenchSurface.catalog.label}>{label}</dt>
      <dd className="mt-1 min-w-0 truncate text-sm font-medium text-[var(--theme-text)]">
        {value || "-"}
      </dd>
    </div>
  );
}

function SectionTitle({
  icon,
  title,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-3 flex min-w-0 items-center gap-2">
      <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
        {icon}
      </span>
      <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-[var(--theme-text)]">
        {title}
      </h2>
      {action}
    </div>
  );
}

function EmptyBlock({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-[var(--theme-border)] px-4 py-5 text-center text-sm leading-6 text-[var(--theme-text-secondary)]">
      {children}
    </div>
  );
}

function JsonInline({ value }: { value: AgentWorkspaceJsonValue | undefined }) {
  if (value === undefined || value === null) return null;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return <span>{String(value)}</span>;
  }
  if (Array.isArray(value)) {
    return <span>{value.map((item) => String(item)).join(", ")}</span>;
  }
  return (
    <span>
      {Object.entries(value)
        .map(([key, item]) => `${key}: ${String(item)}`)
        .join(" · ")}
    </span>
  );
}

function AgentIdentity({
  projection,
  selectedAgentId,
  onSelectAgent,
}: {
  projection: AgentWorkspaceProjection;
  selectedAgentId: string | null;
  onSelectAgent: (agentId: string | null) => void;
}) {
  const { t } = useTranslation();
  const selected = projection.selected_agent;
  return (
    <section className={`${workbenchSurface.panel} p-4`}>
      <SectionTitle
        icon={<Bot size={16} />}
        title={t("agentWorkspace.identity", "Agent identity")}
        action={
          selected ? (
            <StatusChip tone={statusTone(selected.status)}>
              {selected.status}
            </StatusChip>
          ) : null
        }
      />
      {selected ? (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div className="min-w-0">
            <h3 className="truncate text-lg font-semibold text-[var(--theme-text)]">
              {selected.name}
            </h3>
            <p className="mt-2 text-sm leading-6 text-[var(--theme-text-secondary)]">
              {selected.description ||
                t("agentWorkspace.noAgentDescription", "No description published.")}
            </p>
            <dl className="mt-4 grid gap-3 sm:grid-cols-3">
              <FieldValue
                label={t("agentWorkspace.agentId", "Agent")}
                value={selected.agent_id}
              />
              <FieldValue
                label={t("agentWorkspace.capability", "Capability")}
                value={selected.capability_id}
              />
              <FieldValue
                label={t("agentWorkspace.version", "Version")}
                value={selected.version}
              />
            </dl>
          </div>
          <div className="min-w-0 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] p-3">
            <p className={workbenchSurface.catalog.label}>
              {t("agentWorkspace.switchAgent", "Switch Agent")}
            </p>
            <div className="mt-2 grid gap-1.5">
              {projection.agents.length === 0 ? (
                <p className="text-xs leading-5 text-[var(--theme-text-secondary)]">
                  {t("agentWorkspace.noAgents", "No Agents are visible.")}
                </p>
              ) : (
                projection.agents.map((agent) => {
                  const active =
                    (selectedAgentId ?? selected.agent_id) === agent.agent_id;
                  return (
                    <button
                      key={agent.agent_id}
                      type="button"
                      onClick={() => onSelectAgent(agent.agent_id)}
                      data-active={active ? "true" : "false"}
                      className="flex min-h-9 min-w-0 items-center justify-between gap-2 rounded-md px-2 text-left text-xs text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-workbench-panel)] data-[active=true]:bg-[var(--theme-workbench-panel)] data-[active=true]:text-[var(--theme-text)]"
                    >
                      <span className="min-w-0 truncate">{agent.name}</span>
                      {active ? <CheckCircle2 size={14} /> : null}
                    </button>
                  );
                })
              )}
            </div>
          </div>
        </div>
      ) : (
        <EmptyBlock>
          {t("agentWorkspace.noSelectedAgent", "No Agent projection is available.")}
        </EmptyBlock>
      )}
    </section>
  );
}

function SessionRunColumn({
  sessions,
  runs,
  selectedSessionId,
  onSelectSession,
}: {
  sessions: AgentWorkspaceSession[];
  runs: AgentWorkspaceRunSummary[];
  selectedSessionId: string | null;
  onSelectSession: (sessionId: string | null) => void;
}) {
  const { t } = useTranslation();
  return (
    <section className={`${workbenchSurface.panel} min-h-0 p-4`}>
      <SectionTitle
        icon={<Clock3 size={16} />}
        title={t("agentWorkspace.sessionsAndRuns", "Sessions and runs")}
      />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="min-w-0">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className={workbenchSurface.catalog.label}>
              {t("agentWorkspace.recentSessions", "Recent sessions")}
            </p>
            {selectedSessionId ? (
              <button
                type="button"
                onClick={() => onSelectSession(null)}
                className="text-xs font-medium text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)]"
              >
                {t("common.clear", "Clear")}
              </button>
            ) : null}
          </div>
          {sessions.length === 0 ? (
            <EmptyBlock>
              {t("agentWorkspace.noSessions", "No recent sessions.")}
            </EmptyBlock>
          ) : (
            <div className="grid gap-1.5">
              {sessions.map((session) => {
                const active = selectedSessionId === session.session_id;
                return (
                  <button
                    key={session.session_id}
                    type="button"
                    onClick={() => onSelectSession(session.session_id)}
                    data-active={active ? "true" : "false"}
                    className="min-w-0 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-3 text-left transition-colors hover:border-[var(--theme-border-strong)] data-[active=true]:border-[var(--theme-primary)]"
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-sm font-medium text-[var(--theme-text)]">
                        {session.title || session.session_id}
                      </span>
                      {active ? <CheckCircle2 size={15} /> : null}
                    </div>
                    <p className="mt-1 truncate text-xs text-[var(--theme-text-secondary)]">
                      {session.updated_at
                        ? formatDateTimeShort(session.updated_at)
                        : session.session_id}
                    </p>
                  </button>
                );
              })}
            </div>
          )}
        </div>
        <div className="min-w-0">
          <p className={`mb-2 ${workbenchSurface.catalog.label}`}>
            {t("agentWorkspace.latestRuns", "Latest runs")}
          </p>
          {runs.length === 0 ? (
            <EmptyBlock>
              {t("agentWorkspace.noRuns", "No runs are visible.")}
            </EmptyBlock>
          ) : (
            <div className="grid gap-2">
              {runs.map((run) => (
                <article
                  key={run.run_id}
                  className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-3"
                >
                  <div className="flex min-w-0 items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="truncate text-sm font-semibold text-[var(--theme-text)]">
                        {run.run_id}
                      </h3>
                      <p className="mt-1 truncate text-xs text-[var(--theme-text-secondary)]">
                        {run.session_id}
                      </p>
                    </div>
                    <StatusChip tone={statusTone(run.status)}>
                      {run.status}
                    </StatusChip>
                  </div>
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[var(--theme-bg-sidebar)]">
                    <div
                      className="h-full rounded-full bg-[var(--theme-primary)]"
                      style={{ width: `${Math.max(0, Math.min(100, run.progress))}%` }}
                    />
                  </div>
                  {run.result_summary ? (
                    <p className="mt-3 line-clamp-3 text-sm leading-6 text-[var(--theme-text-secondary)]">
                      {run.result_summary}
                    </p>
                  ) : null}
                  {run.error_message ? (
                    <p className="mt-2 text-sm leading-6 text-[var(--theme-danger)]">
                      {run.error_message}
                    </p>
                  ) : null}
                </article>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function ArtifactPreview({ artifacts }: { artifacts: AgentWorkspaceArtifact[] }) {
  const { t } = useTranslation();
  return (
    <section className={`${workbenchSurface.panel} p-4`}>
      <SectionTitle
        icon={<FileText size={16} />}
        title={t("agentWorkspace.artifactPreview", "Artifact preview")}
      />
      {artifacts.length === 0 ? (
        <EmptyBlock>
          {t("agentWorkspace.noArtifacts", "No revealed artifacts for this run.")}
        </EmptyBlock>
      ) : (
        <div className="grid gap-2 md:grid-cols-2">
          {artifacts.map((artifact) => (
            <article
              key={artifact.artifact_id ?? artifact.id}
              className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-3"
            >
              <div className="flex min-w-0 items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="truncate text-sm font-semibold text-[var(--theme-text)]">
                    {artifact.label ?? artifact.artifact_id ?? artifact.id}
                  </h3>
                  <p className="mt-1 truncate text-xs text-[var(--theme-text-secondary)]">
                    {artifact.artifact_type ?? artifact.content_type ?? "artifact"}
                  </p>
                </div>
                <StatusChip tone={statusTone(artifact.status)}>
                  {artifact.status ?? "available"}
                </StatusChip>
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
                <FieldValue
                  label={t("agentWorkspace.size", "Size")}
                  value={
                    artifact.size_bytes !== undefined
                      ? `${artifact.size_bytes} B`
                      : "-"
                  }
                />
                <FieldValue
                  label={t("agentWorkspace.download", "Download")}
                  value={
                    artifact.download_url ? (
                      <a
                        className="text-[var(--theme-primary)] hover:underline"
                        href={artifact.download_url}
                      >
                        {t("runPlayback.download", "Download")}
                      </a>
                    ) : (
                      "-"
                    )
                  }
                />
              </dl>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function ConsoleLine({ event }: { event: AgentWorkspaceConsoleEvent }) {
  return (
    <div className="grid grid-cols-[3rem_minmax(0,1fr)] gap-2 border-b border-[var(--theme-border)] py-2 last:border-b-0">
      <span className="font-mono text-[11px] text-[var(--theme-text-tertiary)]">
        #{event.sequence ?? 0}
      </span>
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <StatusChip tone={statusTone(event.severity)}>{event.severity ?? "info"}</StatusChip>
          <span className="truncate font-mono text-xs text-[var(--theme-text-secondary)]">
            {event.stage ?? event.event_type ?? event.type ?? "event"}
          </span>
        </div>
        {event.message ? (
          <p className="mt-1 break-words text-xs leading-5 text-[var(--theme-text)]">
            {event.message}
          </p>
        ) : null}
        {event.payload?.tool_permission_card ? (
          <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
            <JsonInline value={event.payload.tool_permission_card} />
          </p>
        ) : null}
      </div>
    </div>
  );
}

function StepLine({ step }: { step: AgentWorkspaceConsoleStep }) {
  return (
    <div className="grid grid-cols-[3rem_minmax(0,1fr)] gap-2 border-b border-[var(--theme-border)] py-2 last:border-b-0">
      <span className="font-mono text-[11px] text-[var(--theme-text-tertiary)]">
        #{step.sequence ?? 0}
      </span>
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <StatusChip tone={statusTone(step.status)}>{step.status ?? "step"}</StatusChip>
          <span className="truncate text-xs font-medium text-[var(--theme-text)]">
            {step.title ?? step.step_key ?? step.step_id}
          </span>
        </div>
        {step.role ? (
          <p className="mt-1 truncate text-xs text-[var(--theme-text-secondary)]">
            {step.role}
          </p>
        ) : null}
        {step.payload?.summary ? (
          <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
            <JsonInline value={step.payload.summary} />
          </p>
        ) : null}
      </div>
    </div>
  );
}

function RunConsole({
  projection,
}: {
  projection: AgentWorkspaceProjection;
}) {
  const { t } = useTranslation();
  const consoleState = projection.run_console;
  return (
    <aside className={`${workbenchSurface.panel} flex min-h-0 flex-col p-4`}>
      <SectionTitle
        icon={<SquareTerminal size={16} />}
        title={t("agentWorkspace.runConsole", "Run Console")}
        action={
          <StatusChip tone={statusTone(consoleState.status)}>
            {consoleState.status}
          </StatusChip>
        }
      />
      <div className="mb-3 grid grid-cols-2 gap-2">
        <div className={workbenchSurface.statusTile}>
          <p className={workbenchSurface.catalog.label}>
            {t("agentWorkspace.runId", "Run")}
          </p>
          <p className="mt-1 truncate font-mono text-xs text-[var(--theme-text)]">
            {consoleState.run_id ?? t("workbench.noRun", "No active run")}
          </p>
        </div>
        <div className={workbenchSurface.statusTile}>
          <p className={workbenchSurface.catalog.label}>
            {t("agentWorkspace.cursor", "Cursor")}
          </p>
          <p className="mt-1 font-mono text-xs text-[var(--theme-text)]">
            {consoleState.next_after_sequence}
          </p>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-3 py-2">
        {consoleState.events.length === 0 && consoleState.steps.length === 0 ? (
          <EmptyBlock>
            {t("agentWorkspace.noConsoleEvents", "No console events yet.")}
          </EmptyBlock>
        ) : (
          <div className="grid gap-3">
            {consoleState.events.length > 0 ? (
              <section>
                <p className={`mb-1 ${workbenchSurface.catalog.label}`}>
                  {t("agentWorkspace.events", "Events")}
                </p>
                {consoleState.events.map((event, index) => (
                  <ConsoleLine
                    key={event.event_id ?? event.id ?? `${event.sequence}-${index}`}
                    event={event}
                  />
                ))}
              </section>
            ) : null}
            {consoleState.steps.length > 0 ? (
              <section>
                <p className={`mb-1 ${workbenchSurface.catalog.label}`}>
                  {t("agentWorkspace.steps", "Steps")}
                </p>
                {consoleState.steps.map((step, index) => (
                  <StepLine
                    key={step.step_id ?? step.id ?? `${step.sequence}-${index}`}
                    step={step}
                  />
                ))}
              </section>
            ) : null}
          </div>
        )}
      </div>
    </aside>
  );
}

function ApprovalSummary({
  permissions,
}: {
  permissions: AgentWorkspaceToolPermission[];
}) {
  const { t } = useTranslation();
  return (
    <section className={`${workbenchSurface.panel} p-4`}>
      <SectionTitle
        icon={<ShieldCheck size={16} />}
        title={t("agentWorkspace.pendingApprovals", "Pending approvals")}
        action={
          <StatusChip tone={permissions.length > 0 ? "warning" : "success"}>
            {permissions.length}
          </StatusChip>
        }
      />
      {permissions.length === 0 ? (
        <EmptyBlock>
          {t("agentWorkspace.noApprovals", "No tool permission is waiting.")}
        </EmptyBlock>
      ) : (
        <div className="grid gap-2">
          {permissions.map((permission) => (
            <article
              key={permission.permission_request_id}
              className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-3"
            >
              <div className="flex min-w-0 items-center justify-between gap-2">
                <h3 className="min-w-0 truncate text-sm font-semibold text-[var(--theme-text)]">
                  {permission.tool_id}
                </h3>
                <StatusChip tone={statusTone(permission.risk_level)}>
                  {permission.risk_level}
                </StatusChip>
              </div>
              <p className="mt-1 truncate text-xs text-[var(--theme-text-secondary)]">
                {permission.action} · {permission.run_id}
              </p>
              {permission.reason ? (
                <p className="mt-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
                  {permission.reason}
                </p>
              ) : null}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function MemoryContextSummary({
  projection,
}: {
  projection: AgentWorkspaceProjection;
}) {
  const { t } = useTranslation();
  const policy = projection.memory_context_policy;
  const context = policy.latest_context;
  const materials = context?.referenced_materials;
  const counts = [
    [t("workbench.messages", "Messages"), materials?.message_count ?? 0],
    [t("workbench.artifacts", "Artifacts"), materials?.artifact_count ?? 0],
    [t("workbench.contextPanel.files", "Files"), materials?.file_count ?? 0],
    [t("runPlayback.status.total", "Total"), materials?.memory_record_count ?? 0],
  ] as const;

  return (
    <section className={`${workbenchSurface.panel} p-4`}>
      <SectionTitle
        icon={<Brain size={16} />}
        title={t("agentWorkspace.memoryContext", "Memory context")}
        action={
          <StatusChip tone={policy.memory_enabled ? "success" : "neutral"}>
            {policy.memory_enabled
              ? t("governance.enabled", "Enabled")
              : t("governance.disabled", "Disabled")}
          </StatusChip>
        }
      />
      <dl className="grid grid-cols-2 gap-2">
        <FieldValue
          label={t("agentWorkspace.retention", "Retention")}
          value={`${policy.retention_days}d`}
        />
        <FieldValue
          label={t("agentWorkspace.redaction", "Redaction")}
          value={policy.redaction_mode}
        />
        <FieldValue
          label={t("agentWorkspace.policySource", "Source")}
          value={policy.source}
        />
        <FieldValue
          label={t("agentWorkspace.contextSource", "Context")}
          value={context?.source}
        />
      </dl>
      <div className="mt-3 grid grid-cols-4 gap-2">
        {counts.map(([label, count]) => (
          <div key={label} className={workbenchSurface.statusTile}>
            <p className="text-[11px] text-[var(--theme-text-tertiary)]">
              {label}
            </p>
            <p className="mt-1 text-sm font-semibold text-[var(--theme-text)]">
              {count}
            </p>
          </div>
        ))}
      </div>
      {policy.reason ? (
        <p className="mt-3 text-xs leading-5 text-[var(--theme-text-secondary)]">
          {policy.reason}
        </p>
      ) : null}
    </section>
  );
}

export function AgentWorkspacePanel() {
  const { t } = useTranslation();
  const { isAuthenticated, isLoading: authLoading } = useAuth();
  const [projection, setProjection] = useState<AgentWorkspaceProjection | null>(
    null,
  );
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const workspaceId = "default";

  const loadProjection = useCallback(async () => {
    if (!isAuthenticated) return;
    setIsLoading(true);
    setLoadError(null);
    try {
      const response = await fetchAgentWorkspace({
        workspace_id: workspaceId,
        agent_id: selectedAgentId,
        session_id: selectedSessionId,
      });
      setProjection(response);
    } catch (error) {
      setProjection(null);
      setLoadError(errorMessage(error));
    } finally {
      setIsLoading(false);
    }
  }, [isAuthenticated, selectedAgentId, selectedSessionId]);

  useEffect(() => {
    void loadProjection();
  }, [loadProjection]);

  const governanceState = resolveFrontendGovernanceState({
    isAuthenticated,
    isLoading: authLoading || (isLoading && !projection && !loadError),
    hasWorkspace: Boolean(workspaceId),
    hasPermission: !isPermissionError(loadError),
    featureEnabled: true,
    projectionError: loadError,
  });

  const availability = useMemo(
    () => ({
      projection: resolveGroupAvailability({
        backed: !loadError,
        enabled: Boolean(projection && !loadError),
      }),
      console: resolveGroupAvailability({
        backed: !loadError,
        enabled: Boolean(projection?.run_console.run_id),
      }),
      artifacts: resolveGroupAvailability({
        backed: !loadError,
        enabled: Boolean(projection?.artifacts.length),
      }),
    }),
    [loadError, projection],
  );

  const refreshAction = (
    <button
      type="button"
      className="btn-primary"
      onClick={loadProjection}
      disabled={isLoading || !isAuthenticated}
    >
      <RefreshCw size={14} className={isLoading ? "animate-spin" : ""} />
      <span className="hidden sm:inline">{t("common.refresh", "Refresh")}</span>
    </button>
  );

  if (governanceState !== "ready" && governanceState !== "degraded") {
    return (
      <div
        data-agent-workspace-shell
        {...buildFrontendGovernanceSmokeAttributes(governanceState)}
        className={workbenchSurface.statePage}
      >
        <WorkbenchStateSurface
          state={governanceState}
          surface="agent-workspace"
          details={loadError ? [loadError] : undefined}
        />
      </div>
    );
  }

  if (!projection) {
    return (
      <div
        data-agent-workspace-shell
        {...buildFrontendGovernanceSmokeAttributes(governanceState)}
        className={workbenchSurface.page}
      >
        <PanelHeader
          title={t("agentWorkspace.title", "Agent Workspace")}
          subtitle={t(
            "agentWorkspace.subtitle",
            "Read-only Agent workspace from the governed projection.",
          )}
          icon={<SquareTerminal size={20} />}
          actions={refreshAction}
        />
        <div className="flex-1 px-4 py-4">
          <WorkbenchStateSurface
            state={governanceState}
            surface="agent-workspace-projection"
            title={
              governanceState === "degraded"
                ? t("agentWorkspace.degradedTitle", "Agent Workspace is degraded")
                : undefined
            }
            description={
              governanceState === "degraded"
                ? t(
                    "agentWorkspace.degradedDescription",
                    "The workspace projection request did not return data.",
                  )
                : undefined
            }
            details={loadError ? [loadError] : undefined}
            className="max-w-none"
          />
        </div>
      </div>
    );
  }

  return (
    <div
      data-agent-workspace-shell
      {...buildFrontendGovernanceSmokeAttributes(governanceState)}
      className={workbenchSurface.page}
    >
      <PanelHeader
        title={t("agentWorkspace.title", "Agent Workspace")}
        subtitle={t(
          "agentWorkspace.subtitle",
          "Read-only view of Agent identity, sessions, runs, artifacts, approvals, and memory context.",
        )}
        icon={<SquareTerminal size={20} />}
        actions={
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            <GovernanceAvailabilityBadge
              state={availability.projection.state}
              labelKey={availability.projection.labelKey}
            />
            <GovernanceAvailabilityBadge
              state={availability.console.state}
              labelKey={availability.console.labelKey}
            />
            {refreshAction}
          </div>
        }
      />

      {governanceState === "degraded" ? (
        <div className="px-4 pb-3">
          <WorkbenchStateSurface
            state="degraded"
            surface="agent-workspace-degraded"
            details={loadError ? [loadError] : undefined}
            className="max-w-none"
          />
        </div>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-3 overflow-hidden px-4 pb-4 xl:grid-cols-[minmax(0,1fr)_24rem]">
        <main className="min-h-0 overflow-y-auto pr-0 xl:pr-1">
          <div className="grid gap-3">
            <section className="grid gap-3 sm:grid-cols-3">
              <div className={workbenchSurface.statusTile}>
                <div className="flex items-center gap-2">
                  <ListChecks size={15} className="text-[var(--theme-text-secondary)]" />
                  <p className={workbenchSurface.catalog.label}>
                    {t("agentWorkspace.sessions", "Sessions")}
                  </p>
                </div>
                <p className="mt-2 text-xl font-semibold text-[var(--theme-text)]">
                  {projection.sessions.length}
                </p>
              </div>
              <div className={workbenchSurface.statusTile}>
                <div className="flex items-center gap-2">
                  <SquareTerminal size={15} className="text-[var(--theme-text-secondary)]" />
                  <p className={workbenchSurface.catalog.label}>
                    {t("agentWorkspace.runs", "Runs")}
                  </p>
                </div>
                <p className="mt-2 text-xl font-semibold text-[var(--theme-text)]">
                  {projection.latest_runs.length}
                </p>
              </div>
              <div className={workbenchSurface.statusTile}>
                <div className="flex items-center gap-2">
                  <AlertTriangle size={15} className="text-[var(--theme-text-secondary)]" />
                  <p className={workbenchSurface.catalog.label}>
                    {t("agentWorkspace.approvals", "Approvals")}
                  </p>
                </div>
                <p className="mt-2 text-xl font-semibold text-[var(--theme-text)]">
                  {projection.pending_tool_permissions.length}
                </p>
              </div>
            </section>

            <AgentIdentity
              projection={projection}
              selectedAgentId={selectedAgentId}
              onSelectAgent={(agentId) => {
                setSelectedAgentId(agentId);
                setSelectedSessionId(null);
              }}
            />
            <SessionRunColumn
              sessions={projection.sessions}
              runs={projection.latest_runs}
              selectedSessionId={selectedSessionId}
              onSelectSession={setSelectedSessionId}
            />
            <ArtifactPreview artifacts={projection.artifacts} />
          </div>
        </main>

        <div className="grid min-h-0 gap-3 overflow-hidden">
          <RunConsole projection={projection} />
          <div className="min-h-0 overflow-y-auto">
            <div className="grid gap-3">
              <ApprovalSummary
                permissions={projection.pending_tool_permissions}
              />
              <MemoryContextSummary projection={projection} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default AgentWorkspacePanel;
