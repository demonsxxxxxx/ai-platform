import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  type LucideIcon,
  Bell,
  CheckCircle2,
  MessageSquareText,
  RotateCcw,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Users,
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
  }
> = {
  users: {
    title: "workbench.projections.users.title",
    subtitle: "workbench.projections.users.subtitle",
    icon: Users,
    surface: "workbench-users-projection",
    readPermission: Permission.USER_READ,
    adminPermission: Permission.USER_ADMIN,
  },
  settings: {
    title: "workbench.projections.settings.title",
    subtitle: "workbench.projections.settings.subtitle",
    icon: Settings,
    surface: "workbench-settings-projection",
    readPermission: Permission.SETTINGS_READ,
    adminPermission: Permission.SETTINGS_ADMIN,
  },
  feedback: {
    title: "workbench.projections.feedback.title",
    subtitle: "workbench.projections.feedback.subtitle",
    icon: MessageSquareText,
    surface: "workbench-feedback-projection",
    readPermission: Permission.FEEDBACK_READ,
    adminPermission: Permission.FEEDBACK_ADMIN,
  },
  notifications: {
    title: "workbench.projections.notifications.title",
    subtitle: "workbench.projections.notifications.subtitle",
    icon: Bell,
    surface: "workbench-notifications-projection",
    readPermission: Permission.NOTIFICATION_READ,
    adminPermission: Permission.NOTIFICATION_ADMIN,
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
        <section data-projection-workbench-grid className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="min-w-0 space-y-4">
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
      <div className="border-b border-[var(--theme-border)] p-4">
        <p className={workbenchSurface.label}>
          {t("workbench.governedRoute.contractTitle")}
        </p>
        <h2 className="mt-1 text-sm font-semibold text-[var(--theme-text)]">
          {t("workbench.projections.governance.safeReadTitle")}
        </h2>
        <p className="mt-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
          {t("workbench.governedRoute.contractDescription")}
        </p>
      </div>

      <div className="space-y-2 p-3">
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
    <div className="rounded-md bg-[var(--theme-bg-sidebar)] p-3 ring-1 ring-[var(--theme-border)]">
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
        <div className="border-b border-[var(--theme-border)] p-3">
          <div className="relative">
            <Search
              size={17}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-stone-400"
            />
            <input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              className="panel-search h-10 pl-9"
              placeholder={t("workbench.projections.users.search")}
            />
          </div>
        </div>
        <div className="divide-y divide-[var(--theme-border)]">
          {rows.length === 0 ? (
            <EmptyProjection message={t("workbench.projections.users.empty")} />
          ) : (
            rows.map((user) => (
              <article
                key={user.id}
                className="grid gap-3 p-3 text-sm sm:grid-cols-[minmax(0,1fr)_14rem]"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <CheckCircle2
                      size={15}
                      className={user.is_active ? "text-emerald-600" : "text-stone-400"}
                    />
                    <h3 className="truncate font-semibold text-[var(--theme-text)]">
                      {user.full_name || user.username}
                    </h3>
                  </div>
                  <p className="mt-1 truncate text-xs text-[var(--theme-text-secondary)]">
                    {user.username} · {user.tenant_id}/{user.department_id || "-"}
                  </p>
                </div>
                <div className="flex min-w-0 flex-wrap gap-1.5">
                  {user.roles.slice(0, 4).map((role) => (
                    <span
                      key={role}
                      className="rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 text-xs text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]"
                    >
                      {role}
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
              <div className="border-b border-[var(--theme-border)] p-3">
                <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                  {group.category.replace(/_/g, " ")}
                </h3>
              </div>
              <div className="divide-y divide-[var(--theme-border)]">
                {group.items.map((item) => (
                  <div key={item.key} className="grid gap-2 p-3 text-sm">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h4 className="truncate font-medium text-[var(--theme-text)]">
                          {item.label || item.key}
                        </h4>
                        <p className="mt-1 truncate text-xs text-[var(--theme-text-secondary)]">
                          {item.key}
                        </p>
                      </div>
                      {item.is_secret || item.audit_required ? (
                        <GovernanceAvailabilityBadge
                          state={item.audit_required ? "enabled" : "disabled"}
                          labelKey={
                            item.audit_required
                              ? "governance.enabled"
                              : "governance.disabled"
                          }
                        />
                      ) : null}
                    </div>
                    <p className="truncate rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1.5 text-xs text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
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
        <div className="divide-y divide-[var(--theme-border)]">
          {feedback.data?.items.length ? (
            feedback.data.items.map((item) => (
              <article key={item.id} className="grid gap-2 p-3 text-sm">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="font-semibold text-[var(--theme-text)]">
                      {item.rating === "up"
                        ? t("workbench.projections.feedback.positive")
                        : t("workbench.projections.feedback.negative")}
                    </h3>
                    <p className="mt-1 truncate text-xs text-[var(--theme-text-secondary)]">
                      {item.username} · {item.session_id} · {item.run_id}
                    </p>
                  </div>
                  <span className="rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 text-xs text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
                    {item.status} / {item.assignment_state}
                  </span>
                </div>
                {item.comment ? (
                  <p className="rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1.5 text-xs leading-5 text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
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
  const unreadCount = combined.filter((item) => item.read_state === "unread").length;
  const activeCount = combined.filter((item) => item.is_active).length;
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
          value: combined.length,
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
        <div className="divide-y divide-[var(--theme-border)]">
          {combined.length === 0 ? (
            <EmptyProjection message={t("workbench.projections.notifications.empty")} />
          ) : (
            combined.map((item) => (
              <article key={`${item.id}-${item.read_state ?? "admin"}`} className="p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate text-sm font-semibold text-[var(--theme-text)]">
                      {localizedText(item.title_i18n, i18n.language)}
                    </h3>
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
                      {localizedText(item.content_i18n, i18n.language)}
                    </p>
                  </div>
                  <span className="rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 text-xs text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
                    {item.read_state ?? item.type}
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
