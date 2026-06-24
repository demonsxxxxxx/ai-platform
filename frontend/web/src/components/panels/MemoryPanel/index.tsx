import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Brain,
  Database,
  History,
  LockKeyhole,
  RefreshCw,
  Save,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import toast from "react-hot-toast";
import { PanelHeader } from "../../common/PanelHeader";
import { GovernanceAvailabilityBadge } from "../../governance/GovernanceAvailabilityBadge";
import {
  isPermissionError,
  resolveFrontendGovernanceState,
} from "../../governance/frontendGovernanceState";
import { resolveGroupAvailability } from "../../governance/groupAvailability";
import { WorkbenchStateSurface } from "../../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../../workbench/workbenchSurface";
import { useAuth } from "../../../hooks/useAuth";
import { formatDateTimeShort } from "../../../utils/datetime";
import {
  cleanupExpiredMemoryRecords,
  deleteMemoryRecord,
  fetchAdminMemoryPolicies,
  fetchAdminMemoryRecords,
  fetchMemoryPolicy,
  fetchMemoryRecords,
  setMemoryPolicy,
  type AiPlatformMemoryRecord,
  type MemoryPolicy,
} from "../../../services/api/memory";

const MEMORY_ADMIN_ROLES = new Set([
  "admin",
  "tenant_admin",
  "platform_admin",
  "break_glass_admin",
]);

function roleCanUseAdminMemory(roles: string[] | undefined): boolean {
  return Boolean(
    roles?.some((role) => MEMORY_ADMIN_ROLES.has(role.trim().toLowerCase())),
  );
}

function normalizedOptionalId(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function FieldLabel({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="min-w-0">
      <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--theme-text-secondary)]">
        {label}
      </span>
      {children}
    </label>
  );
}

function TextInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <input
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      className="enterprise-form-input"
    />
  );
}

function StatusPill({
  active,
  children,
}: {
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${
        active
          ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
          : "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]"
      }`}
    >
      {children}
    </span>
  );
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="enterprise-empty-state enterprise-empty-state--compact text-sm">
      {children}
    </div>
  );
}

function MemoryRecordRow({
  record,
  canDelete,
  onDelete,
}: {
  record: AiPlatformMemoryRecord;
  canDelete?: boolean;
  onDelete?: (recordId: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="enterprise-subtle-panel">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill active={record.status === "active"}>
          {record.status || "active"}
        </StatusPill>
        <span className="text-xs text-[var(--theme-text-secondary)]">
          {record.record_type}
        </span>
        <span className="text-xs text-[var(--theme-text-secondary)]">
          {record.agent_id || "agent"}
        </span>
        <span className="ml-auto text-xs text-[var(--theme-text-secondary)]">
          {record.created_at ? formatDateTimeShort(record.created_at) : ""}
        </span>
        {canDelete && onDelete && (
          <button
            type="button"
            aria-label={t("memory.workbench.deleteRecord")}
            className="btn-icon inline-flex h-9 w-9 items-center justify-center rounded-lg text-red-500 transition hover:bg-red-50 dark:hover:bg-red-500/10"
            onClick={() => onDelete(record.memory_record_id)}
            title={t("common.delete")}
          >
            <Trash2 size={15} />
          </button>
        )}
      </div>
      {record.content && (
        <p className="mt-2 line-clamp-3 text-sm leading-relaxed text-[var(--theme-text)]">
          {record.content}
        </p>
      )}
      <div className="mt-2 flex flex-wrap gap-1.5">
        <span className="es-chip">{record.memory_record_id}</span>
        {record.session_id && <span className="es-chip">{record.session_id}</span>}
        {record.expires_at && (
          <span className="es-chip">
            {t("memory.workbench.expires", {
              date: formatDateTimeShort(record.expires_at),
            })}
          </span>
        )}
      </div>
    </div>
  );
}

function PolicySummary({ policy }: { policy: MemoryPolicy | null }) {
  const { t } = useTranslation();
  if (!policy) {
    return <EmptyState>{t("memory.workbench.noPolicy")}</EmptyState>;
  }
  return (
    <div className="grid gap-2 text-sm text-[var(--theme-text-secondary)] sm:grid-cols-2">
      <div>
        <span className="font-medium text-[var(--theme-text)]">
          {t("memory.workbench.user")}
        </span>{" "}
        {policy.user_id}
      </div>
      <div>
        <span className="font-medium text-[var(--theme-text)]">
          {t("memory.workbench.agent")}
        </span>{" "}
        {policy.agent_id || t("memory.workbench.allAgents")}
      </div>
      <div>
        <span className="font-medium text-[var(--theme-text)]">
          {t("memory.workbench.retention")}
        </span>{" "}
        {t("memory.workbench.days", { count: policy.retention_days })}
      </div>
      <div>
        <span className="font-medium text-[var(--theme-text)]">
          {t("memory.workbench.source")}
        </span>{" "}
        {policy.source}
      </div>
      <div className="sm:col-span-2">
        <span className="font-medium text-[var(--theme-text)]">
          {t("memory.workbench.updated")}
        </span>{" "}
        {policy.updated_at
          ? formatDateTimeShort(policy.updated_at)
          : t("memory.workbench.defaultPolicy")}
      </div>
      {policy.reason && (
        <div className="sm:col-span-2">
          <span className="font-medium text-[var(--theme-text)]">
            {t("memory.workbench.reason")}
          </span>{" "}
          {policy.reason}
        </div>
      )}
    </div>
  );
}

export function MemoryPanel() {
  const { t } = useTranslation();
  const {
    user,
    isAuthenticated,
    isLoading: authLoading,
  } = useAuth();
  const [workspaceId, setWorkspaceId] = useState("default");
  const [agentId, setAgentId] = useState("document-review");
  const [sessionId, setSessionId] = useState("");
  const [policy, setPolicy] = useState<MemoryPolicy | null>(null);
  const [memoryEnabled, setMemoryEnabled] = useState(true);
  const [retentionDays, setRetentionDays] = useState(90);
  const [reason, setReason] = useState("");
  const [records, setRecords] = useState<AiPlatformMemoryRecord[]>([]);
  const [adminPolicies, setAdminPolicies] = useState<MemoryPolicy[]>([]);
  const [adminRecords, setAdminRecords] = useState<AiPlatformMemoryRecord[]>([]);
  const [policyLoading, setPolicyLoading] = useState(false);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [adminLoading, setAdminLoading] = useState(false);
  const [cleanupLoading, setCleanupLoading] = useState(false);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [recordsError, setRecordsError] = useState<string | null>(null);
  const [adminError, setAdminError] = useState<string | null>(null);
  const recordsRequestSeq = useRef(0);

  const canUseAdminMemory = useMemo(
    () => roleCanUseAdminMemory(user?.roles),
    [user?.roles],
  );
  const normalizedWorkspaceId = normalizedOptionalId(workspaceId) ?? "default";
  const normalizedAgentId = normalizedOptionalId(agentId);
  const normalizedSessionId = normalizedOptionalId(sessionId);
  const topLevelProjectionError =
    policyError || (canUseAdminMemory ? adminError : null);
  const governanceState = resolveFrontendGovernanceState({
    isAuthenticated,
    isLoading: authLoading || (policyLoading && !policy && !policyError),
    hasWorkspace: Boolean(normalizedWorkspaceId),
    hasPermission: !isPermissionError(topLevelProjectionError),
    featureEnabled: true,
    projectionError: topLevelProjectionError,
    degraded: Boolean(canUseAdminMemory && adminError),
  });
  const policyAvailability = resolveGroupAvailability({
    backed: !policyError,
    enabled: Boolean(policy && !policyError),
  });
  const recordsAvailability = resolveGroupAvailability({
    backed: !recordsError,
    enabled: Boolean(normalizedSessionId && !recordsError),
  });
  const adminAvailability = resolveGroupAvailability({
    backed: canUseAdminMemory ? !adminError : true,
    enabled: canUseAdminMemory && !adminError,
    adminOnly: !canUseAdminMemory,
  });

  const loadPolicy = useCallback(async () => {
    setPolicyLoading(true);
    setPolicyError(null);
    try {
      const response = await fetchMemoryPolicy({
        workspace_id: normalizedWorkspaceId,
        agent_id: normalizedAgentId,
      });
      setPolicy(response.memory_policy);
      setMemoryEnabled(response.memory_policy.memory_enabled);
      setRetentionDays(response.memory_policy.retention_days);
    } catch (error) {
      const message = errorMessage(error);
      setPolicyError(message);
      toast.error(message);
      setPolicy(null);
    } finally {
      setPolicyLoading(false);
    }
  }, [normalizedAgentId, normalizedWorkspaceId]);

  const loadRecords = useCallback(async () => {
    const requestSeq = recordsRequestSeq.current + 1;
    recordsRequestSeq.current = requestSeq;
    if (!normalizedSessionId) {
      setRecords([]);
      setRecordsError(null);
      return;
    }
    setRecordsLoading(true);
    setRecordsError(null);
    try {
      const response = await fetchMemoryRecords({
        workspace_id: normalizedWorkspaceId,
        agent_id: normalizedAgentId,
        session_id: normalizedSessionId,
        limit: 50,
      });
      if (recordsRequestSeq.current === requestSeq) {
        setRecords(response.memory_records);
      }
    } catch (error) {
      if (recordsRequestSeq.current === requestSeq) {
        const message = errorMessage(error);
        setRecordsError(message);
        toast.error(message);
        setRecords([]);
      }
    } finally {
      if (recordsRequestSeq.current === requestSeq) {
        setRecordsLoading(false);
      }
    }
  }, [normalizedAgentId, normalizedSessionId, normalizedWorkspaceId]);

  const loadAdminProjection = useCallback(async () => {
    if (!canUseAdminMemory) {
      setAdminPolicies([]);
      setAdminRecords([]);
      setAdminError(null);
      return;
    }
    setAdminLoading(true);
    setAdminError(null);
    try {
      const [policyResponse, recordResponse] = await Promise.all([
        fetchAdminMemoryPolicies({
          workspace_id: normalizedWorkspaceId,
          agent_id: normalizedAgentId,
          limit: 25,
        }),
        fetchAdminMemoryRecords({
          workspace_id: normalizedWorkspaceId,
          status: "active",
          limit: 25,
        }),
      ]);
      setAdminPolicies(policyResponse.memory_policies);
      setAdminRecords(recordResponse.memory_records);
    } catch (error) {
      setAdminError(errorMessage(error));
      setAdminPolicies([]);
      setAdminRecords([]);
    } finally {
      setAdminLoading(false);
    }
  }, [canUseAdminMemory, normalizedAgentId, normalizedWorkspaceId]);

  useEffect(() => {
    loadPolicy();
  }, [loadPolicy]);

  useEffect(() => {
    loadRecords();
  }, [loadRecords]);

  useEffect(() => {
    loadAdminProjection();
  }, [loadAdminProjection]);

  const refreshAll = async () => {
    await Promise.all([loadPolicy(), loadRecords(), loadAdminProjection()]);
  };

  const savePolicy = async () => {
    setPolicyLoading(true);
    setPolicyError(null);
    try {
      const response = await setMemoryPolicy({
        workspace_id: normalizedWorkspaceId,
        agent_id: normalizedAgentId,
        memory_enabled: memoryEnabled,
        long_term_memory_enabled: false,
        retention_days: retentionDays,
        reason,
      });
      setPolicy(response.memory_policy);
      toast.success(t("memory.workbench.policyUpdated"));
      await Promise.all([loadRecords(), loadAdminProjection()]);
    } catch (error) {
      const message = errorMessage(error);
      setPolicyError(message);
      toast.error(message);
    } finally {
      setPolicyLoading(false);
    }
  };

  const removeRecord = async (recordId: string) => {
    if (!normalizedSessionId) return;
    if (!window.confirm(t("memory.workbench.deleteRecordConfirm"))) return;
    try {
      await deleteMemoryRecord(recordId, {
        workspace_id: normalizedWorkspaceId,
        agent_id: normalizedAgentId,
        session_id: normalizedSessionId,
        reason: "user deleted from Memory panel",
      });
      toast.success(t("memory.workbench.recordDeleted"));
      await Promise.all([loadRecords(), loadAdminProjection()]);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  const runRetentionCleanup = async () => {
    setCleanupLoading(true);
    try {
      const response = await cleanupExpiredMemoryRecords({
        workspace_id: normalizedWorkspaceId,
        limit: 200,
      });
      toast.success(
        t("memory.workbench.cleanupDeleted", {
          count: response.deleted_count,
        }),
      );
      await loadAdminProjection();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setCleanupLoading(false);
    }
  };

  const refreshAction = (
    <button
      type="button"
      className="btn-primary"
      onClick={refreshAll}
      disabled={policyLoading || recordsLoading || adminLoading}
    >
      <RefreshCw
        size={14}
        className={
          policyLoading || recordsLoading || adminLoading ? "animate-spin" : ""
        }
      />
      <span className="hidden sm:inline">{t("common.refresh", "Refresh")}</span>
    </button>
  );

  const blockingGovernanceState =
    governanceState === "loading" ||
    governanceState === "logged-out" ||
    governanceState === "no-workspace" ||
    governanceState === "forbidden";

  if (blockingGovernanceState) {
    return (
      <div
        data-memory-workbench-shell
        data-frontend-governance-state={governanceState}
        className="flex h-full min-h-0 flex-col bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]"
      >
        <PanelHeader
          title={t("memory.title", "Memory")}
          subtitle={t("memory.workbench.subtitle")}
          icon={<Brain size={20} />}
          actions={refreshAction}
        />

        <div className="flex min-h-0 flex-1 items-center justify-center px-4 py-4">
          <WorkbenchStateSurface
            state={governanceState}
            surface="memory-workbench-governance"
            title={
              governanceState === "forbidden"
                ? t("workbench.states.forbidden.title")
                : undefined
            }
            description={
              governanceState === "forbidden"
                ? t("memory.workbench.forbiddenDescription")
                : undefined
            }
            details={topLevelProjectionError ? [topLevelProjectionError] : undefined}
          />
        </div>
      </div>
    );
  }

  return (
    <div
      data-memory-workbench-shell
      data-frontend-governance-state={governanceState}
      className="flex h-full min-h-0 flex-col bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]"
    >
      <PanelHeader
        title={t("memory.title", "Memory")}
        subtitle={t("memory.workbench.subtitle")}
        icon={<Brain size={20} />}
        actions={refreshAction}
      />

      <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        {governanceState === "degraded" ? (
          <WorkbenchStateSurface
            state="degraded"
            surface="memory-workbench-degraded"
            title={t("memory.workbench.degradedTitle")}
            description={t("memory.workbench.degradedDescription")}
            details={
              topLevelProjectionError ? [topLevelProjectionError] : undefined
            }
            className="mb-4 max-w-none text-left"
          />
        ) : null}

        <section className="mb-4 grid gap-3 lg:grid-cols-3">
          <div className={workbenchSurface.compactPanel}>
            <div className="flex items-start justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <ShieldCheck
                    size={16}
                    className="text-stone-500 dark:text-stone-400"
                  />
                  <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                    {t("memory.workbench.policyProjection")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t("memory.workbench.policyProjectionDescription")}
                </p>
              </div>
              <GovernanceAvailabilityBadge
                state={policyAvailability.state}
                labelKey={policyAvailability.labelKey}
              />
            </div>
          </div>
          <div className={workbenchSurface.compactPanel}>
            <div className="flex items-start justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <History
                    size={16}
                    className="text-stone-500 dark:text-stone-400"
                  />
                  <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                    {t("memory.workbench.sessionRecordsProjection")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t("memory.workbench.sessionRecordsProjectionDescription")}
                </p>
              </div>
              <GovernanceAvailabilityBadge
                state={recordsAvailability.state}
                labelKey={recordsAvailability.labelKey}
              />
            </div>
          </div>
          <div
            data-fail-closed-surface="memory-admin-governance"
            className={workbenchSurface.compactPanel}
          >
            <div className="flex items-start justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <LockKeyhole
                    size={16}
                    className="text-stone-500 dark:text-stone-400"
                  />
                  <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                    {t("memory.workbench.adminGovernance")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t("memory.workbench.adminGovernanceDescription")}
                </p>
              </div>
              <GovernanceAvailabilityBadge
                state={adminAvailability.state}
                labelKey={adminAvailability.labelKey}
              />
            </div>
          </div>
        </section>

        <div className="mb-4 grid gap-3 lg:grid-cols-3">
          <FieldLabel label={t("memory.workbench.workspace")}>
            <TextInput value={workspaceId} onChange={setWorkspaceId} />
          </FieldLabel>
          <FieldLabel label={t("memory.workbench.agentPublicId")}>
            <TextInput
              value={agentId}
              onChange={setAgentId}
              placeholder="document-review"
            />
          </FieldLabel>
          <FieldLabel label={t("memory.workbench.sessionId")}>
            <TextInput
              value={sessionId}
              onChange={setSessionId}
              placeholder={t("memory.workbench.sessionIdPlaceholder")}
            />
          </FieldLabel>
        </div>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <section className={`${workbenchSurface.panel} p-4 sm:p-5`}>
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <ShieldCheck size={18} className="text-[var(--theme-text-secondary)]" />
              <h2 className="text-base font-semibold text-[var(--theme-text)]">
                {t("memory.workbench.userPolicy")}
              </h2>
              <div className="ml-auto flex flex-wrap gap-2">
                <StatusPill active={memoryEnabled}>
                  {memoryEnabled
                    ? t("governance.enabled")
                    : t("governance.disabled")}
                </StatusPill>
                <StatusPill active={false}>
                  {t("memory.workbench.longTermClosed")}
                </StatusPill>
              </div>
            </div>

            <div className="grid gap-3">
              <label className="flex items-center justify-between gap-3 enterprise-subtle-panel">
                <span>
                  <span className="block text-sm font-medium text-[var(--theme-text)]">
                    {t("memory.workbench.allowSessionWrites")}
                  </span>
                  <span className="block text-xs text-[var(--theme-text-secondary)]">
                    {t("memory.workbench.allowSessionWritesHint")}
                  </span>
                </span>
                <input
                  type="checkbox"
                  checked={memoryEnabled}
                  onChange={(event) => setMemoryEnabled(event.target.checked)}
                  className="h-5 w-5 accent-[var(--theme-primary)]"
                />
              </label>

              <FieldLabel label={t("memory.workbench.retentionDays")}>
                <input
                  type="number"
                  min={1}
                  max={3650}
                  value={retentionDays}
                  onChange={(event) => {
                    const next = Number(event.target.value);
                    if (Number.isFinite(next)) {
                      setRetentionDays(Math.max(1, Math.min(3650, next)));
                    }
                  }}
                  className="enterprise-form-input"
                />
              </FieldLabel>

              <FieldLabel label={t("memory.workbench.reason")}>
                <TextInput
                  value={reason}
                  onChange={setReason}
                  placeholder={t("memory.workbench.reasonPlaceholder")}
                />
              </FieldLabel>

              <button
                type="button"
                className="btn-primary justify-center"
                onClick={savePolicy}
                disabled={policyLoading}
              >
                <Save size={15} />
                {t("memory.workbench.savePolicy")}
              </button>
            </div>

            <div className="mt-4 border-t border-[var(--theme-border)] pt-4">
              <PolicySummary policy={policy} />
            </div>
          </section>

          <section className={`${workbenchSurface.panel} p-4 sm:p-5`}>
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <Database size={18} className="text-[var(--theme-text-secondary)]" />
              <h2 className="text-base font-semibold text-[var(--theme-text)]">
                {t("memory.workbench.sessionRecords")}
              </h2>
              <span className="ml-auto text-xs text-[var(--theme-text-secondary)]">
                {t("memory.workbench.publicProjectionOnly")}
              </span>
            </div>

            {recordsError ? (
              <EmptyState>{recordsError}</EmptyState>
            ) : !normalizedSessionId ? (
              <EmptyState>
                {t("memory.workbench.sessionRequired")}
              </EmptyState>
            ) : recordsLoading ? (
              <EmptyState>{t("memory.workbench.loadingRecords")}</EmptyState>
            ) : records.length === 0 ? (
              <EmptyState>{t("memory.workbench.noSessionRecords")}</EmptyState>
            ) : (
              <div className="grid gap-3">
                {records.map((record) => (
                  <MemoryRecordRow
                    key={record.memory_record_id}
                    record={record}
                    canDelete
                    onDelete={removeRecord}
                  />
                ))}
              </div>
            )}
          </section>
        </div>

        <section className={`${workbenchSurface.panel} mt-4 p-4 sm:p-5`}>
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <ShieldCheck size={18} className="text-[var(--theme-text-secondary)]" />
            <h2 className="text-base font-semibold text-[var(--theme-text)]">
              {t("memory.workbench.adminProjection")}
            </h2>
            {canUseAdminMemory && (
              <button
                type="button"
                className="btn-secondary ml-auto"
                onClick={runRetentionCleanup}
                disabled={cleanupLoading}
              >
                <Trash2 size={15} />
                <span>{t("memory.workbench.retentionCleanup")}</span>
              </button>
            )}
          </div>

          {!canUseAdminMemory ? (
            <EmptyState>
              {t("memory.workbench.adminHidden")}
            </EmptyState>
          ) : adminError ? (
            <EmptyState>{adminError}</EmptyState>
          ) : adminLoading ? (
            <EmptyState>{t("memory.workbench.loadingAdmin")}</EmptyState>
          ) : (
            <div className="grid gap-4 xl:grid-cols-2">
              <div>
                <h3 className="mb-2 text-sm font-semibold text-[var(--theme-text)]">
                  {t("memory.workbench.storedPolicies")}
                </h3>
                {adminPolicies.length === 0 ? (
                  <EmptyState>{t("memory.workbench.noStoredPolicies")}</EmptyState>
                ) : (
                  <div className="grid gap-2">
                    {adminPolicies.map((item) => (
                      <div
                        key={`${item.workspace_id}:${item.user_id}:${item.agent_id || "all"}`}
                        className="enterprise-subtle-panel text-sm"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusPill active={item.memory_enabled}>
                            {item.memory_enabled
                              ? t("governance.enabled")
                              : t("governance.disabled")}
                          </StatusPill>
                          <span className="font-medium text-[var(--theme-text)]">
                            {item.user_id}
                          </span>
                          <span className="text-[var(--theme-text-secondary)]">
                            {item.agent_id || t("memory.workbench.allAgents")}
                          </span>
                        </div>
                        <div className="mt-2 text-xs text-[var(--theme-text-secondary)]">
                          {t("memory.workbench.policyMeta", {
                            days: item.retention_days,
                            source: item.source,
                            date: item.updated_at
                              ? formatDateTimeShort(item.updated_at)
                              : t("memory.workbench.defaultPolicy"),
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-[var(--theme-text)]">
                  {t("memory.workbench.activeRecords")}
                </h3>
                {adminRecords.length === 0 ? (
                  <EmptyState>{t("memory.workbench.noAdminRecords")}</EmptyState>
                ) : (
                  <div className="grid gap-2">
                    {adminRecords.map((record) => (
                      <MemoryRecordRow
                        key={record.memory_record_id}
                        record={record}
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
