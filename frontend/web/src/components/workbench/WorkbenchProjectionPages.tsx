import { Button, Input } from "@lobehub/ui/base-ui";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  type LucideIcon,
  Activity,
  Bug,
  CheckCircle2,
  RotateCcw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Users,
  UserCheck,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { PanelHeader } from "../common/PanelHeader";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import {
  buildFrontendGovernanceSmokeAttributes,
  resolveFrontendGovernanceState,
  type FrontendGovernanceState,
} from "../governance/frontendGovernanceState";
import { resolveGroupAvailability } from "../governance/groupAvailability";
import { useAuth } from "../../hooks/useAuth";
import {
  adminUsersApi,
  type AdminUserDiagnosticsResponse,
  type AdminUserSummary,
} from "../../services/api/adminUsers";
import type { WorkbenchGovernance } from "../../services/api/workbench";
import { Permission } from "../../types";
import { formatDateTimeShort } from "../../utils/datetime";
import { AdminUserDiagnosticsDrawer } from "./AdminUserDiagnosticsDrawer";
import { ModelGroupUserDrawer } from "./ModelGroupUserDrawer";
import { WorkbenchStateSurface } from "./WorkbenchStateSurface";
import { workbenchSurface } from "./workbenchSurface";

type LoadState<T> = {
  data: T | null;
  error: string | null;
  isLoading: boolean;
};

type PageKind = "users";

type AvailabilityState = "enabled" | "disabled" | "inherited" | "admin-only" | "unavailable";

type ProjectionStatusTone = "primary" | "muted" | "warning" | "danger";

type ProjectionMetric = {
  label: string;
  value: string | number;
  detail?: string;
  icon?: LucideIcon;
};

const pageMeta: Record<
  PageKind,
  {
    title: string;
    subtitle: string;
    icon: typeof Users;
    surface: string;
    readPermission: Permission;
    adminPermission: Permission;
    taskTitle: string;
    taskDescription: string;
  }
> = {
  users: {
    title: "workbench.projections.users.title",
    subtitle: "workbench.projections.users.subtitle",
    icon: Users,
    surface: "workbench-users-projection",
    readPermission: Permission.USER_READ,
    adminPermission: Permission.USER_ADMIN,
    taskTitle: "workbench.projections.users.taskTitle",
    taskDescription: "workbench.projections.users.taskDescription",
  },
};

function useProjection<T>(loader: () => Promise<T>, deps: unknown[]): LoadState<T> {
  const [state, setState] = useState<LoadState<T>>({
    data: null,
    error: null,
    isLoading: true,
  });

  useEffect(() => {
    let cancelled = false;
    setState((current) => ({ ...current, error: null, isLoading: true }));

    loader()
      .then((data) => {
        if (!cancelled) setState({ data, error: null, isLoading: false });
      })
      .catch((error) => {
        if (!cancelled) {
          setState({
            data: null,
            error:
              error instanceof Error
                ? error.message
                : "workbench.projections.loadFailed",
            isLoading: false,
          });
        }
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}

function governanceState(
  state: LoadState<unknown>,
  governance?: WorkbenchGovernance | null,
): FrontendGovernanceState {
  return resolveFrontendGovernanceState({
    isAuthenticated: true,
    isLoading: state.isLoading,
    hasPermission: true,
    hasWorkspace: governance ? Boolean(governance.workspace_id) : true,
    projectionError: state.error,
    degraded: Boolean(governance?.degraded),
  });
}

function projectionStatusToneClass(tone: ProjectionStatusTone) {
  switch (tone) {
    case "primary":
      return "border-[color-mix(in_srgb,var(--theme-primary)_28%,var(--theme-border))] bg-[color-mix(in_srgb,var(--theme-primary)_8%,var(--theme-workbench-panel))] text-[var(--theme-primary)]";
    case "warning":
      return "border-[var(--theme-warning-ring)] bg-[var(--theme-warning-soft)] text-[var(--theme-warning)]";
    case "danger":
      return "border-[var(--theme-danger-ring)] bg-[var(--theme-danger-soft)] text-[var(--theme-danger-text)]";
    case "muted":
    default:
      return "border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]";
  }
}

function ProjectionShell({
  kind,
  loadState,
  governance,
  auditAvailable,
  metrics,
  children,
}: {
  kind: PageKind;
  loadState: LoadState<unknown>;
  governance?: WorkbenchGovernance | null;
  auditAvailable?: boolean;
  metrics?: ProjectionMetric[];
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const meta = pageMeta[kind];
  const Icon = meta.icon;
  const state = governanceState(loadState, governance);
  const secretMaterialProjected = Boolean(
    governance && governance.secret_material_projected,
  );
  const readAvailability = resolveGroupAvailability({
    backed: true,
    enabled: state === "ready" || state === "degraded",
  });
  const adminAvailability = resolveGroupAvailability({
    backed: true,
    adminOnly: !hasPermission(meta.adminPermission),
    enabled: hasPermission(meta.adminPermission),
  });
  const details = [
    loadState.error,
    governance?.projection
      ? t("workbench.projections.governance.projection", {
          projection: governance.projection,
          workspace: governance.workspace_id,
        })
      : null,
    secretMaterialProjected
      ? t("workbench.projections.governance.secretProjected")
      : null,
  ].filter((item): item is string => Boolean(item));

  if (state === "loading" || state === "forbidden") {
    return (
      <div
        data-workbench-projection-page={kind}
        {...buildFrontendGovernanceSmokeAttributes(state)}
        className="flex h-full min-h-0 items-center justify-center bg-[var(--theme-workbench-canvas)] px-4"
      >
        <WorkbenchStateSurface
          state={state}
          surface={meta.surface}
          title={
            state === "forbidden"
              ? t("workbench.projections.forbidden.title")
              : t("workbench.projections.loading.title")
          }
          description={
            state === "forbidden"
              ? t("workbench.projections.forbidden.description", {
                  permission: meta.readPermission,
                })
              : t("workbench.projections.loading.description")
          }
          details={details}
        />
      </div>
    );
  }

  return (
    <div
      data-workbench-projection-page={kind}
      {...buildFrontendGovernanceSmokeAttributes(state)}
      className="flex h-full min-h-0 flex-col bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]"
    >
      <PanelHeader
        title={t(meta.title)}
        subtitle={t(meta.subtitle)}
        icon={<Icon size={20} className="text-theme-text-secondary" />}
        actions={
          <div className="flex items-center gap-2">
            <GovernanceAvailabilityBadge
              state={readAvailability.state}
              labelKey={readAvailability.labelKey}
            />
            <GovernanceAvailabilityBadge
              state={adminAvailability.state}
              labelKey={adminAvailability.labelKey}
            />
          </div>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5">
        {state === "degraded" ? (
          <WorkbenchStateSurface
            state="degraded"
            surface={meta.surface}
            title={t("workbench.projections.degraded.title")}
            description={t("workbench.projections.degraded.description")}
            details={details}
            className="mb-3 max-w-none text-left"
          />
        ) : null}
        <section
          data-projection-workbench-grid
          className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_18rem]"
        >
          <div className="min-w-0 space-y-4">
            <section
              data-projection-task-panel
              className={`${workbenchSurface.compactPanel} px-4 py-3`}
            >
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className={workbenchSurface.label}>
                    {t("workbench.projections.currentTask")}
                  </p>
                  <h2 className="mt-1 text-sm font-semibold text-[var(--theme-text)]">
                    {t(meta.taskTitle)}
                  </h2>
                  <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
                    {t(meta.taskDescription)}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap gap-2">
                  <GovernanceAvailabilityBadge
                    state={readAvailability.state}
                    labelKey={readAvailability.labelKey}
                  />
                  <GovernanceAvailabilityBadge
                    state={adminAvailability.state}
                    labelKey={adminAvailability.labelKey}
                  />
                </div>
              </div>
            </section>
            {metrics?.length ? (
              <section data-projection-summary-panel className="grid gap-3 sm:grid-cols-3">
                {metrics.map((metric) => (
                  <ProjectionMetric key={metric.label} {...metric} />
                ))}
              </section>
            ) : null}
            <ProjectionListPanel>{children}</ProjectionListPanel>
          </div>

          <ProjectionInsightPanel
            details={details}
            readAvailability={readAvailability}
            adminAvailability={adminAvailability}
            auditAvailable={Boolean(
              auditAvailable ??
                (governance?.audit_required || governance?.rollback_available),
            )}
            secretMaterialProjected={secretMaterialProjected}
          />
        </section>
      </div>
    </div>
  );
}

function ProjectionMetric({
  label,
  value,
  detail,
  icon: Icon = CheckCircle2,
}: ProjectionMetric) {
  return (
    <div data-projection-metric className={workbenchSurface.statusTile}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className={workbenchSurface.label}>{label}</p>
          <p className="mt-1 truncate text-xl font-semibold tabular-nums text-[var(--theme-text)]">
            {value}
          </p>
          {detail ? (
            <p className="mt-1 truncate text-xs text-[var(--theme-text-secondary)]">
              {detail}
            </p>
          ) : null}
        </div>
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
          <Icon size={16} />
        </div>
      </div>
    </div>
  );
}

function ProjectionInsightPanel({
  details,
  readAvailability,
  adminAvailability,
  auditAvailable,
  secretMaterialProjected,
}: {
  details: string[];
  readAvailability: { state: AvailabilityState; labelKey: string };
  adminAvailability: { state: AvailabilityState; labelKey: string };
  auditAvailable: boolean;
  secretMaterialProjected: boolean;
}) {
  const { t } = useTranslation();

  return (
    <aside
      data-projection-insight-panel
      className={`${workbenchSurface.panel} h-fit overflow-hidden`}
    >
      <div className="border-b border-[var(--theme-border)] px-4 py-3">
        <p className={workbenchSurface.label}>
          {t("workbench.governedRoute.contractTitle")}
        </p>
        <h2 className="mt-1 text-sm font-semibold text-[var(--theme-text)]">
          {t("workbench.projections.governance.summaryTitle")}
        </h2>
      </div>

      <div className="space-y-2 p-2.5">
        <StatusTile
          icon={ShieldCheck}
          title={t("workbench.projections.governance.safeReadTitle")}
          description={t("workbench.projections.governance.safeReadDescription")}
          state={readAvailability.state}
          labelKey={readAvailability.labelKey}
        />
        <StatusTile
          icon={SlidersHorizontal}
          title={t("workbench.projections.governance.adminTitle")}
          description={t("workbench.projections.governance.adminDescription")}
          state={adminAvailability.state}
          labelKey={adminAvailability.labelKey}
        />
        <StatusTile
          icon={RotateCcw}
          title={t("workbench.projections.governance.auditTitle")}
          description={
            auditAvailable
              ? t("workbench.projections.governance.auditBacked")
              : t("workbench.projections.governance.auditUnavailable")
          }
          state={auditAvailable ? "enabled" : "disabled"}
          labelKey={auditAvailable ? "governance.enabled" : "governance.disabled"}
        />
        <StatusTile
          icon={ShieldCheck}
          title={t("workbench.projections.governance.redactionTitle")}
          description={t("workbench.projections.governance.redactionDescription")}
          state={secretMaterialProjected ? "enabled" : "disabled"}
          labelKey={
            secretMaterialProjected ? "governance.enabled" : "governance.disabled"
          }
        />
      </div>

      {details.length ? (
        <div className="border-t border-[var(--theme-border)] px-4 py-3">
          <p className={`${workbenchSurface.label} mb-1`}>
            {t("workbench.projections.governance.scope")}
          </p>
          {details.map((detail) => (
            <p
              key={detail}
              className="py-1 text-xs leading-5 text-[var(--theme-text-secondary)]"
            >
              {detail}
            </p>
          ))}
        </div>
      ) : null}
    </aside>
  );
}

function ProjectionListPanel({ children }: { children: ReactNode }) {
  return (
    <section data-projection-list-panel className="min-w-0">
      {children}
    </section>
  );
}

function StatusTile({
  icon: Icon,
  title,
  description,
  state,
  labelKey,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  state: AvailabilityState;
  labelKey: string;
}) {
  return (
    <div className="rounded-md bg-[var(--theme-workbench-panel)] p-3 ring-1 ring-[var(--theme-border)]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Icon size={16} className="text-[var(--theme-text-secondary)]" />
            <h3 className="text-sm font-semibold text-[var(--theme-text)]">
              {title}
            </h3>
          </div>
          <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
            {description}
          </p>
        </div>
        <GovernanceAvailabilityBadge state={state} labelKey={labelKey} />
      </div>
    </div>
  );
}

function ProjectionStatusChip({
  children,
  tone = "muted",
}: {
  children: ReactNode;
  tone?: ProjectionStatusTone;
}) {
  return (
    <span
      data-projection-status-chip
      data-projection-status-tone={tone}
      className={`inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium ${projectionStatusToneClass(tone)}`}
    >
      {children}
    </span>
  );
}

export function WorkbenchUsersProjectionPanel() {
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedUser, setSelectedUser] = useState<AdminUserSummary | null>(null);
  const [diagnostics, setDiagnostics] = useState<AdminUserDiagnosticsResponse | null>(null);
  const [diagnosticsError, setDiagnosticsError] = useState<string | null>(null);
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(false);
  const [modelGroupUser, setModelGroupUser] = useState<AdminUserSummary | null>(null);
  const modelGroupTriggerRef = useRef<HTMLElement | null>(null);
  const diagnosticsRequest = useRef(0);
  const users = useProjection(
    () => adminUsersApi.list({ limit: 50, search: searchQuery }),
    [searchQuery],
  );
  const rows = users.data?.users ?? [];
  const { hasAnyPermission } = useAuth();
  const canManageModelGroups = hasAnyPermission([Permission.MODEL_ADMIN]);
  useEffect(() => {
    if (!canManageModelGroups) setModelGroupUser(null);
  }, [canManageModelGroups]);
  const activeCount = rows.filter((user) => user.status === "active").length;
  const runCount = rows.reduce((total, user) => total + user.run_count, 0);
  const failedRunCount = rows.reduce((total, user) => total + user.failed_run_count, 0);

  const openDiagnostics = async (user: AdminUserSummary) => {
    const requestId = ++diagnosticsRequest.current;
    setSelectedUser(user);
    setDiagnostics(null);
    setDiagnosticsError(null);
    setDiagnosticsLoading(true);
    try {
      const result = await adminUsersApi.diagnostics(user.user_id);
      if (diagnosticsRequest.current === requestId) setDiagnostics(result);
    } catch (error) {
      if (diagnosticsRequest.current === requestId) {
        setDiagnosticsError(
          error instanceof Error ? error.message : t("workbench.projections.users.diagnosticsLoadFailed"),
        );
      }
    } finally {
      if (diagnosticsRequest.current === requestId) setDiagnosticsLoading(false);
    }
  };

  const closeDiagnostics = () => {
    diagnosticsRequest.current += 1;
    setSelectedUser(null);
    setDiagnostics(null);
    setDiagnosticsError(null);
    setDiagnosticsLoading(false);
  };

  return (
    <>
      <ProjectionShell
        kind="users"
        loadState={users}
        auditAvailable
        metrics={[
          {
            label: t("workbench.projections.users.total"),
            value: users.data?.total ?? rows.length,
            detail: t("workbench.projections.users.visible", { count: rows.length }),
            icon: Users,
          },
          {
            label: t("workbench.projections.users.active"),
            value: activeCount,
            detail: t("workbench.projections.users.inactive", {
              count: Math.max(rows.length - activeCount, 0),
            }),
            icon: CheckCircle2,
          },
          {
            label: t("workbench.projections.users.runs"),
            value: runCount,
            detail: t("workbench.projections.users.failedRuns", { count: failedRunCount }),
            icon: Activity,
          },
        ]}
      >
        <div className={workbenchSurface.compactPanel}>
          <div className="border-b border-[var(--theme-border)] px-4 py-3">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div className="min-w-0">
                <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                  {t("workbench.projections.users.directoryTitle")}
                </h3>
                <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
                  {t("workbench.projections.users.directoryDescription")}
                </p>
              </div>
              <div className="min-w-0 md:w-72">
                <Input
                  prefix={<Search size={17} />}
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  size="large" className="w-full"
                  aria-label={t("workbench.projections.users.search")}
                  placeholder={t("workbench.projections.users.search")}
                />
              </div>
            </div>
          </div>
          <div className="divide-y divide-[var(--theme-border)]">
            {rows.length === 0 ? (
              <EmptyProjection message={t("workbench.projections.users.empty")} />
            ) : (
              rows.map((user) => (
                <article
                  key={user.user_id}
                  className="grid gap-3 px-4 py-3 text-sm md:grid-cols-[minmax(0,1fr)_minmax(18rem,auto)]"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <UserCheck
                        size={15}
                        className={
                          user.status === "active"
                            ? "text-[var(--theme-primary)]"
                            : "text-[var(--theme-text-tertiary)]"
                        }
                      />
                      <h3 className="truncate font-semibold text-[var(--theme-text)]">
                        {user.display_name || user.user_id}
                      </h3>
                      <ProjectionStatusChip tone={user.status === "active" ? "primary" : "muted"}>
                        {user.status === "active"
                          ? t("workbench.projections.users.activeState")
                          : t("workbench.projections.users.inactiveState")}
                      </ProjectionStatusChip>
                    </div>
                    <p className="mt-1 truncate font-mono text-xs text-[var(--theme-text-secondary)]">
                      {user.user_id}
                    </p>
                    <p className="mt-1 text-xs text-[var(--theme-text-tertiary)]">
                      {t("workbench.projections.users.lastActivity")}: {user.last_activity_at ? formatDateTimeShort(user.last_activity_at) : "-"}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-2 md:justify-end">
                    <span className="text-xs text-[var(--theme-text-secondary)]">
                      {t("workbench.projections.users.activitySummary", {
                        sessions: user.session_count,
                        runs: user.run_count,
                        failed: user.failed_run_count,
                      })}
                    </span>
                    {canManageModelGroups ? <Button type="default"
                      htmlType="button"
                      data-user-model-groups={user.user_id}
                      className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs"
                      onClick={(event) => { modelGroupTriggerRef.current = event.currentTarget as HTMLElement; setModelGroupUser(user); }}
                    >
                      <SlidersHorizontal size={14} />模型分组
                    </Button> : null}
                    <Button type="default"
                      htmlType="button"
                      data-user-run-debug={user.user_id}
                      className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs"
                      onClick={() => void openDiagnostics(user)}
                    >
                      <Bug size={14} />
                      {t("workbench.projections.users.auditDebug")}
                    </Button>
                  </div>
                </article>
              ))
            )}
          </div>
        </div>
      </ProjectionShell>

      {modelGroupUser ? <ModelGroupUserDrawer user={modelGroupUser} finalFocus={modelGroupTriggerRef} onClose={() => setModelGroupUser(null)} /> : null}
      {selectedUser ? (
        <AdminUserDiagnosticsDrawer
          user={selectedUser}
          diagnostics={diagnostics}
          error={diagnosticsError}
          loading={diagnosticsLoading}
          onClose={closeDiagnostics}
        />
      ) : null}
    </>
  );
}

function EmptyProjection({ message }: { message: string }) {
  const { t } = useTranslation();
  const items = [
    {
      title: t("workbench.projections.empty.safeReadTitle", "安全投影"),
      description: t(
        "workbench.projections.empty.safeReadDescription",
        "该页面已连接，仅读取受治理的前端数据。",
      ),
    },
    {
      title: t("workbench.projections.empty.noRowsTitle", "未返回可展示的数据"),
      description: message,
    },
    {
      title: t("workbench.projections.empty.nextActionTitle", "下一步操作"),
      description: t(
        "workbench.projections.empty.nextActionDescription",
        "如需创建或修改记录，请使用受管理员治理的流程。",
      ),
    },
  ];

  return (
    <div
      data-projection-empty-state
      className={`${workbenchSurface.emptyState} min-h-44`}
    >
      <div className="flex flex-col gap-3 sm:flex-row">
        {items.map((item, index) => (
          <ProjectionEmptyItem
            key={item.title}
            index={index + 1}
            title={item.title}
            description={item.description}
          />
        ))}
      </div>
    </div>
  );
}

function ProjectionEmptyItem({
  index,
  title,
  description,
}: {
  index: number;
  title: string;
  description: string;
}) {
  return (
    <div className="min-w-0 flex-1 rounded-md bg-[var(--theme-bg-sidebar)] p-3 ring-1 ring-[var(--theme-border)]">
      <div className="flex items-center gap-2">
        <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-[var(--theme-workbench-panel)] text-[11px] font-semibold text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
          {index}
        </span>
        <h3 className="truncate text-sm font-semibold text-[var(--theme-text)]">
          {title}
        </h3>
      </div>
      <p className="mt-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
        {description}
      </p>
    </div>
  );
}
