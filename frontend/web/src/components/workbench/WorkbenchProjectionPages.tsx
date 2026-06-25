import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  type LucideIcon,
  Bell,
  CheckCircle2,
  Clock3,
  MessageSquareText,
  Megaphone,
  RotateCcw,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Users,
  UserCheck,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { PanelHeader } from "../common/PanelHeader";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import {
  resolveFrontendGovernanceState,
  type FrontendGovernanceState,
} from "../governance/frontendGovernanceState";
import { resolveGroupAvailability } from "../governance/groupAvailability";
import { useAuth } from "../../hooks/useAuth";
import { workbenchApi } from "../../services/api/workbench";
import type {
  WorkbenchGovernance,
  WorkbenchNotification,
  WorkbenchNotificationListResponse,
} from "../../services/api/workbench";
import { Permission } from "../../types";
import { WorkbenchStateSurface } from "./WorkbenchStateSurface";
import { workbenchSurface } from "./workbenchSurface";

type LoadState<T> = {
  data: T | null;
  error: string | null;
  isLoading: boolean;
};

type PageKind = "users" | "settings" | "feedback" | "notifications";

type AvailabilityState = "enabled" | "disabled" | "inherited" | "admin-only" | "unavailable";

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
  settings: {
    title: "workbench.projections.settings.title",
    subtitle: "workbench.projections.settings.subtitle",
    icon: Settings,
    surface: "workbench-settings-projection",
    readPermission: Permission.SETTINGS_READ,
    adminPermission: Permission.SETTINGS_ADMIN,
    taskTitle: "workbench.projections.settings.taskTitle",
    taskDescription: "workbench.projections.settings.taskDescription",
  },
  feedback: {
    title: "workbench.projections.feedback.title",
    subtitle: "workbench.projections.feedback.subtitle",
    icon: MessageSquareText,
    surface: "workbench-feedback-projection",
    readPermission: Permission.FEEDBACK_READ,
    adminPermission: Permission.FEEDBACK_ADMIN,
    taskTitle: "workbench.projections.feedback.taskTitle",
    taskDescription: "workbench.projections.feedback.taskDescription",
  },
  notifications: {
    title: "workbench.projections.notifications.title",
    subtitle: "workbench.projections.notifications.subtitle",
    icon: Bell,
    surface: "workbench-notifications-projection",
    readPermission: Permission.NOTIFICATION_READ,
    adminPermission: Permission.NOTIFICATION_ADMIN,
    taskTitle: "workbench.projections.notifications.taskTitle",
    taskDescription: "workbench.projections.notifications.taskDescription",
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
    degraded: Boolean(
      governance?.degraded || governance?.secret_material_projected,
    ),
  });
}

function localizedText(
  value: WorkbenchNotification["title_i18n"],
  language: string,
) {
  if (language.startsWith("zh")) return value.zh || value.en;
  if (language.startsWith("ja")) return value.ja || value.en;
  if (language.startsWith("ko")) return value.ko || value.en;
  if (language.startsWith("ru")) return value.ru || value.en;
  return value.en || value.zh;
}

function formatValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function normalizedLookupKey(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function humanizeToken(value: string) {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function translateMappedValue(
  t: ReturnType<typeof useTranslation>["t"],
  namespace: string,
  value: string | null | undefined,
) {
  const fallback = value ? humanizeToken(value) : "-";
  if (!value) return fallback;
  return t(`${namespace}.${normalizedLookupKey(value)}`, fallback);
}

function roleLabel(t: ReturnType<typeof useTranslation>["t"], role: string) {
  return translateMappedValue(t, "workbench.projections.users.roleLabels", role);
}

function settingCategoryLabel(
  t: ReturnType<typeof useTranslation>["t"],
  category: string,
) {
  return translateMappedValue(
    t,
    "workbench.projections.settings.categories",
    category,
  );
}

function feedbackStatusLabel(
  t: ReturnType<typeof useTranslation>["t"],
  status: string,
) {
  return translateMappedValue(t, "workbench.projections.feedback.status", status);
}

function feedbackAssignmentLabel(
  t: ReturnType<typeof useTranslation>["t"],
  assignmentState: string,
) {
  return translateMappedValue(
    t,
    "workbench.projections.feedback.assignment",
    assignmentState,
  );
}

function notificationStateLabel(
  t: ReturnType<typeof useTranslation>["t"],
  state: string | null,
) {
  return translateMappedValue(
    t,
    "workbench.projections.notifications.readState",
    state,
  );
}

function notificationTypeLabel(
  t: ReturnType<typeof useTranslation>["t"],
  type: string,
) {
  return translateMappedValue(t, "workbench.projections.notifications.type", type);
}

function dedupeNotifications(items: WorkbenchNotification[]) {
  const seen = new Set<string>();
  const deduped: WorkbenchNotification[] = [];
  for (const item of items) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    deduped.push(item);
  }
  return deduped;
}

function ProjectionShell({
  kind,
  loadState,
  governance,
  metrics,
  children,
}: {
  kind: PageKind;
  loadState: LoadState<unknown>;
  governance?: WorkbenchGovernance | null;
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
          tenant: governance.tenant_id,
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
        data-frontend-governance-state={state}
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
      data-frontend-governance-state={state}
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
              governance?.audit_required || governance?.rollback_available,
            )}
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
}: {
  details: string[];
  readAvailability: { state: AvailabilityState; labelKey: string };
  adminAvailability: { state: AvailabilityState; labelKey: string };
  auditAvailable: boolean;
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

export function WorkbenchUsersProjectionPanel() {
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState("");
  const users = useProjection(
    () => workbenchApi.listUsers({ limit: 50, search: searchQuery }),
    [searchQuery],
  );
  const rows = users.data?.items?.length ? users.data.items : users.data?.users ?? [];
  const activeCount = rows.filter((user) => user.is_active).length;
  const roleCount = new Set(rows.flatMap((user) => user.roles)).size;

  return (
    <ProjectionShell
      kind="users"
      loadState={users}
      governance={users.data?.governance}
      metrics={[
        {
          label: t("workbench.projections.users.total"),
          value: users.data?.total ?? rows.length,
          detail: t("workbench.projections.users.visible", {
            count: rows.length,
          }),
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
          label: t("workbench.projections.users.roles"),
          value: roleCount,
          detail: t("workbench.projections.users.rolesDetail"),
          icon: ShieldCheck,
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
            <div className="relative min-w-0 md:w-72">
              <Search
                size={17}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <input
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                className="panel-search h-10 pl-9"
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
                key={user.id}
                className="grid gap-3 px-4 py-3 text-sm md:grid-cols-[minmax(0,1fr)_16rem]"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <UserCheck
                      size={15}
                      className={user.is_active ? "text-emerald-600" : "text-stone-400"}
                    />
                    <h3 className="truncate font-semibold text-[var(--theme-text)]">
                      {user.full_name || user.username}
                    </h3>
                    <span
                      className={`rounded-md px-2 py-0.5 text-xs font-medium ring-1 ${
                        user.is_active
                          ? "bg-emerald-50 text-emerald-700 ring-emerald-100"
                          : "bg-slate-100 text-slate-600 ring-slate-200"
                      }`}
                    >
                      {user.is_active
                        ? t("workbench.projections.users.activeState")
                        : t("workbench.projections.users.inactiveState")}
                    </span>
                  </div>
                  <dl className="mt-2 grid gap-1 text-xs text-[var(--theme-text-secondary)] sm:grid-cols-3">
                    <div>
                      <dt className={workbenchSurface.label}>
                        {t("workbench.projections.users.account")}
                      </dt>
                      <dd className="truncate">{user.username}</dd>
                    </div>
                    <div>
                      <dt className={workbenchSurface.label}>
                        {t("workbench.projections.users.tenant")}
                      </dt>
                      <dd className="truncate">{user.tenant_id}</dd>
                    </div>
                    <div>
                      <dt className={workbenchSurface.label}>
                        {t("workbench.projections.users.department")}
                      </dt>
                      <dd className="truncate">{user.department_id || "-"}</dd>
                    </div>
                  </dl>
                </div>
                <div className="flex min-w-0 flex-wrap content-start gap-1.5">
                  {user.roles.slice(0, 4).map((role) => (
                    <span
                      key={role}
                      className="rounded-md bg-[var(--theme-bg-card)] px-2 py-1 text-xs font-medium text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]"
                    >
                      {roleLabel(t, role)}
                    </span>
                  ))}
                </div>
              </article>
            ))
          )}
        </div>
      </div>
    </ProjectionShell>
  );
}

export function WorkbenchSettingsProjectionPanel() {
  const { t } = useTranslation();
  const settings = useProjection(() => workbenchApi.listSettings(), []);
  const groups = useMemo(
    () => Object.values(settings.data?.settings ?? {}),
    [settings.data?.settings],
  );
  const settingItems = groups.flatMap((group) => group.items);
  const secretCount = settingItems.filter((item) => item.is_secret).length;
  const auditCount = settingItems.filter(
    (item) => item.audit_required || item.rollback_available,
  ).length;

  return (
    <ProjectionShell
      kind="settings"
      loadState={settings}
      governance={settings.data?.governance}
      metrics={[
        {
          label: t("workbench.projections.settings.groups"),
          value: groups.length,
          detail: t("workbench.projections.settings.items", {
            count: settingItems.length,
          }),
          icon: Settings,
        },
        {
          label: t("workbench.projections.settings.redactedCount"),
          value: secretCount,
          detail: t("workbench.projections.settings.redactedDetail"),
          icon: ShieldCheck,
        },
        {
          label: t("workbench.projections.settings.auditCount"),
          value: auditCount,
          detail: t("workbench.projections.settings.auditDetail"),
          icon: RotateCcw,
        },
      ]}
    >
      <div className="grid gap-3 xl:grid-cols-2">
        {groups.length === 0 ? (
          <EmptyProjection message={t("workbench.projections.settings.empty")} />
        ) : (
          groups.map((group) => (
            <section key={group.category} className={workbenchSurface.compactPanel}>
              <div className="border-b border-[var(--theme-border)] px-4 py-3">
                <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                  {settingCategoryLabel(t, group.category)}
                </h3>
                <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
                  {t("workbench.projections.settings.groupItemCount", {
                    count: group.items.length,
                  })}
                </p>
              </div>
              <div className="divide-y divide-[var(--theme-border)]">
                {group.items.map((item) => (
                  <div key={item.key} className="grid gap-2 px-4 py-3 text-sm">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h4 className="truncate font-medium text-[var(--theme-text)]">
                          {item.label || item.key}
                        </h4>
                        <p className="mt-1 truncate text-xs text-[var(--theme-text-secondary)]">
                          {item.key}
                        </p>
                      </div>
                      <div className="flex shrink-0 gap-1.5">
                        {item.is_secret ? (
                          <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600 ring-1 ring-slate-200">
                            {t("workbench.projections.settings.secretChip")}
                          </span>
                        ) : null}
                        {item.audit_required || item.rollback_available ? (
                          <span className="rounded-md bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 ring-1 ring-emerald-100">
                            {t("workbench.projections.settings.auditedChip")}
                          </span>
                        ) : null}
                      </div>
                    </div>
                    <p className="truncate rounded-md bg-[var(--theme-bg-card)] px-2 py-1.5 text-xs text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
                      {item.is_secret
                        ? t("workbench.projections.settings.redacted")
                        : formatValue(item.value)}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          ))
        )}
      </div>
    </ProjectionShell>
  );
}

export function WorkbenchFeedbackProjectionPanel() {
  const { t } = useTranslation();
  const feedback = useProjection(() => workbenchApi.listFeedback({ limit: 50 }), []);

  return (
    <ProjectionShell
      kind="feedback"
      loadState={feedback}
      governance={feedback.data?.governance}
      metrics={[
        {
          label: t("workbench.projections.feedback.total"),
          value: feedback.data?.stats.total_count ?? 0,
          detail: t("workbench.projections.feedback.totalDetail"),
          icon: MessageSquareText,
        },
        {
          label: t("workbench.projections.feedback.up"),
          value: feedback.data?.stats.up_count ?? 0,
          detail: `${feedback.data?.stats.up_percentage ?? 0}%`,
          icon: CheckCircle2,
        },
        {
          label: t("workbench.projections.feedback.down"),
          value: feedback.data?.stats.down_count ?? 0,
          detail: t("workbench.projections.feedback.downDetail"),
          icon: SlidersHorizontal,
        },
      ]}
    >
      <div className={workbenchSurface.compactPanel}>
        <div className="border-b border-[var(--theme-border)] px-4 py-3">
          <h3 className="text-sm font-semibold text-[var(--theme-text)]">
            {t("workbench.projections.feedback.queueTitle")}
          </h3>
          <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
            {t("workbench.projections.feedback.queueDescription")}
          </p>
        </div>
        <div className="divide-y divide-[var(--theme-border)]">
          {feedback.data?.items.length ? (
            feedback.data.items.map((item) => (
              <article key={item.id} className="grid gap-3 px-4 py-3 text-sm">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <MessageSquareText
                        size={15}
                        className={
                          item.rating === "up"
                            ? "text-emerald-600"
                            : "text-rose-600"
                        }
                      />
                      <h3 className="font-semibold text-[var(--theme-text)]">
                      {item.rating === "up"
                        ? t("workbench.projections.feedback.positive")
                        : t("workbench.projections.feedback.negative")}
                      </h3>
                    </div>
                    <dl className="mt-2 grid gap-1 text-xs text-[var(--theme-text-secondary)] sm:grid-cols-3">
                      <div>
                        <dt className={workbenchSurface.label}>
                          {t("workbench.projections.feedback.user")}
                        </dt>
                        <dd className="truncate">{item.username}</dd>
                      </div>
                      <div>
                        <dt className={workbenchSurface.label}>
                          {t("workbench.projections.feedback.session")}
                        </dt>
                        <dd className="truncate">{item.session_id}</dd>
                      </div>
                      <div>
                        <dt className={workbenchSurface.label}>
                          {t("workbench.projections.feedback.run")}
                        </dt>
                        <dd className="truncate">{item.run_id}</dd>
                      </div>
                    </dl>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1.5">
                    <span className="rounded-md bg-[var(--theme-bg-card)] px-2 py-1 text-xs font-medium text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
                      {feedbackStatusLabel(t, item.status)}
                    </span>
                    <span className="rounded-md bg-amber-50 px-2 py-1 text-xs font-medium text-amber-700 ring-1 ring-amber-100">
                      {feedbackAssignmentLabel(t, item.assignment_state)}
                    </span>
                  </div>
                </div>
                {item.comment ? (
                  <p className="rounded-md bg-[var(--theme-bg-card)] px-3 py-2 text-xs leading-5 text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
                    {item.comment}
                  </p>
                ) : null}
              </article>
            ))
          ) : (
            <EmptyProjection message={t("workbench.projections.feedback.empty")} />
          )}
        </div>
      </div>
    </ProjectionShell>
  );
}

export function WorkbenchNotificationsProjectionPanel() {
  const { i18n, t } = useTranslation();
  const { hasPermission } = useAuth();
  const canAdminNotifications = hasPermission(Permission.NOTIFICATION_ADMIN);
  const active = useProjection(() => workbenchApi.listActiveNotifications(), []);
  const admin = useProjection<WorkbenchNotificationListResponse | null>(
    () =>
      canAdminNotifications
        ? workbenchApi.listAdminNotifications({ limit: 50 })
        : Promise.resolve(null),
    [canAdminNotifications],
  );
  const combined: WorkbenchNotification[] = [
    ...(active.data ?? []),
    ...(admin.data?.items ?? []),
  ];
  const visibleNotifications = dedupeNotifications(combined);
  const unreadCount = visibleNotifications.filter(
    (item) => item.read_state === "unread",
  ).length;
  const activeCount = visibleNotifications.filter((item) => item.is_active).length;
  const loadState: LoadState<unknown> = {
    data: active.data,
    error: active.error,
    isLoading: active.isLoading || admin.isLoading,
  };

  return (
    <ProjectionShell
      kind="notifications"
      loadState={loadState}
      governance={admin.data?.governance ?? null}
      metrics={[
        {
          label: t("workbench.projections.notifications.total"),
          value: visibleNotifications.length,
          detail: t("workbench.projections.notifications.totalDetail"),
          icon: Bell,
        },
        {
          label: t("workbench.projections.notifications.unread"),
          value: unreadCount,
          detail: t("workbench.projections.notifications.unreadDetail"),
          icon: MessageSquareText,
        },
        {
          label: t("workbench.projections.notifications.active"),
          value: activeCount,
          detail: t("workbench.projections.notifications.activeDetail"),
          icon: CheckCircle2,
        },
      ]}
    >
      <div className={workbenchSurface.compactPanel}>
        <div className="border-b border-[var(--theme-border)] px-4 py-3">
          <h3 className="text-sm font-semibold text-[var(--theme-text)]">
            {t("workbench.projections.notifications.streamTitle")}
          </h3>
          <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
            {t("workbench.projections.notifications.streamDescription")}
          </p>
        </div>
        <div className="divide-y divide-[var(--theme-border)]">
          {visibleNotifications.length === 0 ? (
            <EmptyProjection message={t("workbench.projections.notifications.empty")} />
          ) : (
            visibleNotifications.map((item) => (
              <article key={item.id} className="px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      {item.type === "announcement" ? (
                        <Megaphone size={15} className="text-teal-700" />
                      ) : (
                        <Bell size={15} className="text-[var(--theme-text-secondary)]" />
                      )}
                      <h3 className="truncate text-sm font-semibold text-[var(--theme-text)]">
                        {localizedText(item.title_i18n, i18n.language)}
                      </h3>
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
                      {localizedText(item.content_i18n, i18n.language)}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2 text-xs text-[var(--theme-text-secondary)]">
                      <span className="inline-flex items-center gap-1 rounded-md bg-[var(--theme-bg-card)] px-2 py-1 ring-1 ring-[var(--theme-border)]">
                        <Clock3 size={12} />
                        {item.created_at || item.updated_at || "-"}
                      </span>
                      <span className="rounded-md bg-[var(--theme-bg-card)] px-2 py-1 ring-1 ring-[var(--theme-border)]">
                        {notificationTypeLabel(t, item.type)}
                      </span>
                    </div>
                  </div>
                  <span
                    className={`rounded-md px-2 py-1 text-xs font-medium ring-1 ${
                      item.read_state === "unread"
                        ? "bg-amber-50 text-amber-700 ring-amber-100"
                        : item.is_active
                          ? "bg-emerald-50 text-emerald-700 ring-emerald-100"
                          : "bg-slate-100 text-slate-600 ring-slate-200"
                    }`}
                  >
                    {notificationStateLabel(
                      t,
                      item.read_state ?? (item.is_active ? "active" : "inactive"),
                    )}
                  </span>
                </div>
              </article>
            ))
          )}
        </div>
      </div>
    </ProjectionShell>
  );
}

function EmptyProjection({ message }: { message: string }) {
  const { t } = useTranslation();
  const items = [
    {
      title: t("workbench.projections.empty.safeReadTitle", "Safe projection"),
      description: t(
        "workbench.projections.empty.safeReadDescription",
        "The page is connected and only reads governed frontend data.",
      ),
    },
    {
      title: t("workbench.projections.empty.noRowsTitle", "No rows returned"),
      description: message,
    },
    {
      title: t("workbench.projections.empty.nextActionTitle", "Next action"),
      description: t(
        "workbench.projections.empty.nextActionDescription",
        "Use admin-governed flows when records need to be created or changed.",
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
        <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-[var(--theme-bg-card)] text-[11px] font-semibold text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
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
