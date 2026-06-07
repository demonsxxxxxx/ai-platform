import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Brain,
  Database,
  RefreshCw,
  Save,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import toast from "react-hot-toast";
import { PanelHeader } from "../../common/PanelHeader";
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
      className="h-10 w-full rounded-lg border border-[var(--glass-border)] bg-[var(--glass-bg)] px-3 text-sm text-[var(--theme-text)] outline-none transition focus:border-[var(--theme-primary)] focus:ring-2 focus:ring-[var(--theme-primary-light)]"
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
          : "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300"
      }`}
    >
      {children}
    </span>
  );
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-[var(--glass-border)] px-4 py-6 text-center text-sm text-[var(--theme-text-secondary)]">
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
  return (
    <div className="rounded-lg border border-[var(--glass-border)] bg-[var(--glass-bg)] p-3">
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
            aria-label="Delete memory record"
            className="btn-icon inline-flex h-9 w-9 items-center justify-center rounded-lg text-red-500 transition hover:bg-red-50 dark:hover:bg-red-500/10"
            onClick={() => onDelete(record.memory_record_id)}
            title="Delete"
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
          <span className="es-chip">expires {formatDateTimeShort(record.expires_at)}</span>
        )}
      </div>
    </div>
  );
}

function PolicySummary({ policy }: { policy: MemoryPolicy | null }) {
  if (!policy) {
    return <EmptyState>No memory policy projection loaded.</EmptyState>;
  }
  return (
    <div className="grid gap-2 text-sm text-[var(--theme-text-secondary)] sm:grid-cols-2">
      <div>
        <span className="font-medium text-[var(--theme-text)]">User</span>{" "}
        {policy.user_id}
      </div>
      <div>
        <span className="font-medium text-[var(--theme-text)]">Agent</span>{" "}
        {policy.agent_id || "all"}
      </div>
      <div>
        <span className="font-medium text-[var(--theme-text)]">Retention</span>{" "}
        {policy.retention_days} days
      </div>
      <div>
        <span className="font-medium text-[var(--theme-text)]">Source</span>{" "}
        {policy.source}
      </div>
      <div className="sm:col-span-2">
        <span className="font-medium text-[var(--theme-text)]">Updated</span>{" "}
        {policy.updated_at ? formatDateTimeShort(policy.updated_at) : "default"}
      </div>
      {policy.reason && (
        <div className="sm:col-span-2">
          <span className="font-medium text-[var(--theme-text)]">Reason</span>{" "}
          {policy.reason}
        </div>
      )}
    </div>
  );
}

export function MemoryPanel() {
  const { t } = useTranslation();
  const { user } = useAuth();
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
  const [adminError, setAdminError] = useState<string | null>(null);
  const recordsRequestSeq = useRef(0);

  const canUseAdminMemory = useMemo(
    () => roleCanUseAdminMemory(user?.roles),
    [user?.roles],
  );
  const normalizedWorkspaceId = normalizedOptionalId(workspaceId) ?? "default";
  const normalizedAgentId = normalizedOptionalId(agentId);
  const normalizedSessionId = normalizedOptionalId(sessionId);

  const loadPolicy = useCallback(async () => {
    setPolicyLoading(true);
    try {
      const response = await fetchMemoryPolicy({
        workspace_id: normalizedWorkspaceId,
        agent_id: normalizedAgentId,
      });
      setPolicy(response.memory_policy);
      setMemoryEnabled(response.memory_policy.memory_enabled);
      setRetentionDays(response.memory_policy.retention_days);
    } catch (error) {
      toast.error(errorMessage(error));
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
      return;
    }
    setRecordsLoading(true);
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
        toast.error(errorMessage(error));
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
      toast.success("Memory policy updated");
      await Promise.all([loadRecords(), loadAdminProjection()]);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setPolicyLoading(false);
    }
  };

  const removeRecord = async (recordId: string) => {
    if (!normalizedSessionId) return;
    if (!window.confirm("Delete this session-bound memory record?")) return;
    try {
      await deleteMemoryRecord(recordId, {
        workspace_id: normalizedWorkspaceId,
        agent_id: normalizedAgentId,
        session_id: normalizedSessionId,
        reason: "user deleted from Memory panel",
      });
      toast.success("Memory record deleted");
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
      toast.success(`Retention cleanup deleted ${response.deleted_count} records`);
      await loadAdminProjection();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setCleanupLoading(false);
    }
  };

  return (
    <div className="glass-shell flex h-full min-h-0 flex-col">
      <PanelHeader
        title={t("memory.title", "Memory")}
        subtitle="ai-platform public memory policy, session records, and admin projection"
        icon={<Brain size={20} />}
        actions={
          <button
            type="button"
            className="btn-primary"
            onClick={refreshAll}
            disabled={policyLoading || recordsLoading || adminLoading}
          >
            <RefreshCw
              size={14}
              className={
                policyLoading || recordsLoading || adminLoading
                  ? "animate-spin"
                  : ""
              }
            />
            <span className="hidden sm:inline">{t("common.refresh", "Refresh")}</span>
          </button>
        }
      />

      <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        <div className="mb-4 grid gap-3 lg:grid-cols-3">
          <FieldLabel label="Workspace">
            <TextInput value={workspaceId} onChange={setWorkspaceId} />
          </FieldLabel>
          <FieldLabel label="Agent public id">
            <TextInput
              value={agentId}
              onChange={setAgentId}
              placeholder="document-review"
            />
          </FieldLabel>
          <FieldLabel label="Session id">
            <TextInput
              value={sessionId}
              onChange={setSessionId}
              placeholder="Required for memory records"
            />
          </FieldLabel>
        </div>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <section className="glass-card rounded-xl p-4 sm:p-5">
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <ShieldCheck size={18} className="text-[var(--theme-text-secondary)]" />
              <h2 className="text-base font-semibold text-[var(--theme-text)]">
                User Memory Policy
              </h2>
              <div className="ml-auto flex flex-wrap gap-2">
                <StatusPill active={memoryEnabled}>
                  {memoryEnabled ? "enabled" : "disabled"}
                </StatusPill>
                <StatusPill active={false}>long-term closed</StatusPill>
              </div>
            </div>

            <div className="grid gap-3">
              <label className="flex items-center justify-between gap-3 rounded-lg border border-[var(--glass-border)] bg-[var(--glass-bg)] px-3 py-3">
                <span>
                  <span className="block text-sm font-medium text-[var(--theme-text)]">
                    Allow session memory writes
                  </span>
                  <span className="block text-xs text-[var(--theme-text-secondary)]">
                    Cross-session long-term memory stays fail-closed.
                  </span>
                </span>
                <input
                  type="checkbox"
                  checked={memoryEnabled}
                  onChange={(event) => setMemoryEnabled(event.target.checked)}
                  className="h-5 w-5 accent-[var(--theme-primary)]"
                />
              </label>

              <FieldLabel label="Retention days">
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
                  className="h-10 w-full rounded-lg border border-[var(--glass-border)] bg-[var(--glass-bg)] px-3 text-sm text-[var(--theme-text)] outline-none transition focus:border-[var(--theme-primary)] focus:ring-2 focus:ring-[var(--theme-primary-light)]"
                />
              </FieldLabel>

              <FieldLabel label="Reason">
                <TextInput
                  value={reason}
                  onChange={setReason}
                  placeholder="Optional public-safe audit reason"
                />
              </FieldLabel>

              <button
                type="button"
                className="btn-primary justify-center"
                onClick={savePolicy}
                disabled={policyLoading}
              >
                <Save size={15} />
                Save Policy
              </button>
            </div>

            <div className="mt-4 border-t border-[var(--glass-border)] pt-4">
              <PolicySummary policy={policy} />
            </div>
          </section>

          <section className="glass-card rounded-xl p-4 sm:p-5">
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <Database size={18} className="text-[var(--theme-text-secondary)]" />
              <h2 className="text-base font-semibold text-[var(--theme-text)]">
                Session Memory Records
              </h2>
              <span className="ml-auto text-xs text-[var(--theme-text-secondary)]">
                public projection only
              </span>
            </div>

            {!normalizedSessionId ? (
              <EmptyState>
                Enter a session id to inspect session-bound memory records.
                Cross-session browsing is intentionally closed.
              </EmptyState>
            ) : recordsLoading ? (
              <EmptyState>Loading session memory records...</EmptyState>
            ) : records.length === 0 ? (
              <EmptyState>No active memory records are visible for this session.</EmptyState>
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

        <section className="glass-card mt-4 rounded-xl p-4 sm:p-5">
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <ShieldCheck size={18} className="text-[var(--theme-text-secondary)]" />
            <h2 className="text-base font-semibold text-[var(--theme-text)]">
              Admin Operational Projection
            </h2>
            {canUseAdminMemory && (
              <button
                type="button"
                className="btn-secondary ml-auto"
                onClick={runRetentionCleanup}
                disabled={cleanupLoading}
              >
                <Trash2 size={15} />
                <span>Retention Cleanup</span>
              </button>
            )}
          </div>

          {!canUseAdminMemory ? (
            <EmptyState>
              Admin memory inventory is hidden for this role. Backend admin
              routes remain fail-closed.
            </EmptyState>
          ) : adminError ? (
            <EmptyState>{adminError}</EmptyState>
          ) : adminLoading ? (
            <EmptyState>Loading admin memory projection...</EmptyState>
          ) : (
            <div className="grid gap-4 xl:grid-cols-2">
              <div>
                <h3 className="mb-2 text-sm font-semibold text-[var(--theme-text)]">
                  Stored Policies
                </h3>
                {adminPolicies.length === 0 ? (
                  <EmptyState>No stored policies for this filter.</EmptyState>
                ) : (
                  <div className="grid gap-2">
                    {adminPolicies.map((item) => (
                      <div
                        key={`${item.workspace_id}:${item.user_id}:${item.agent_id || "all"}`}
                        className="rounded-lg border border-[var(--glass-border)] bg-[var(--glass-bg)] p-3 text-sm"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusPill active={item.memory_enabled}>
                            {item.memory_enabled ? "enabled" : "disabled"}
                          </StatusPill>
                          <span className="font-medium text-[var(--theme-text)]">
                            {item.user_id}
                          </span>
                          <span className="text-[var(--theme-text-secondary)]">
                            {item.agent_id || "all agents"}
                          </span>
                        </div>
                        <div className="mt-2 text-xs text-[var(--theme-text-secondary)]">
                          {item.retention_days}d retention · {item.source} ·{" "}
                          {item.updated_at
                            ? formatDateTimeShort(item.updated_at)
                            : "default"}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-[var(--theme-text)]">
                  Active Records
                </h3>
                {adminRecords.length === 0 ? (
                  <EmptyState>No active records in admin projection.</EmptyState>
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
