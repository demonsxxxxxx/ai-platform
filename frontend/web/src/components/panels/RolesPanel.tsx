import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Building2,
  CheckCircle2,
  GitPullRequestArrow,
  History,
  LockKeyhole,
  RefreshCw,
  RotateCcw,
  Shield,
  UsersRound,
} from "lucide-react";
import toast from "react-hot-toast";
import { useTranslation } from "react-i18next";
import { PanelHeader } from "../common/PanelHeader";
import { PanelLoadingState } from "../common/PanelLoadingState";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { useAuth } from "../../hooks/useAuth";
import { roleGovernanceApi } from "../../services/api/roleGovernance";
import { Permission } from "../../types";
import type {
  RoleGovernanceAuditItem,
  RoleGovernanceOverviewResponse,
  RoleGovernanceRequestItem,
  RoleGovernanceRole,
} from "../../types/roleGovernance";
import { resolveRoleGovernanceState } from "./roleGovernanceState";

type OperationState = {
  kind: "role" | "department" | "approve" | "reject" | "rollback";
  id: string;
} | null;

function getRoleGovernanceLoadErrorMessage(): string {
  return "role-governance-projection-unavailable";
}

function formatCapability(value: string): string {
  return value.replace(/[:_]/g, " ");
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function EmptyBlock({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-36 items-center justify-center p-5 text-center text-sm leading-6 text-[var(--theme-text-secondary)]">
      {children}
    </div>
  );
}

function StatusTile({
  icon: Icon,
  title,
  description,
  state,
  labelKey,
}: {
  icon: typeof Shield;
  title: string;
  description: string;
  state: "enabled" | "disabled" | "inherited" | "admin-only" | "unavailable";
  labelKey: string;
}) {
  return (
    <div className={workbenchSurface.compactPanel}>
      <div className="flex items-start justify-between gap-3 p-3">
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

function RoleCard({
  role,
  canRequestRoles,
  canManageRoles,
  operation,
  onRequestRole,
}: {
  role: RoleGovernanceRole;
  canRequestRoles: boolean;
  canManageRoles: boolean;
  operation: OperationState;
  onRequestRole: (roleId: string) => void;
}) {
  const { t } = useTranslation();
  const requesting = operation?.kind === "role" && operation.id === role.role_id;

  return (
    <article
      data-role-governance-role-card
      className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-4 shadow-[0_4px_12px_rgba(18,38,63,0.03)]"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-[var(--theme-text)]">
              {role.name}
            </h3>
            <span className="rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 text-[11px] font-medium text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
              {role.scope}
            </span>
          </div>
          <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
            {role.role_id}
          </p>
        </div>
        <GovernanceAvailabilityBadge
          state={role.assignable ? "enabled" : role.requestable ? "enabled" : "disabled"}
          labelKey={
            role.assignable || role.requestable
              ? "governance.enabled"
              : "governance.disabled"
          }
        />
      </div>

      <p className="mt-3 line-clamp-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
        {role.description || t("roles.plaza.roleDirectory.noDescription")}
      </p>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {role.capabilities.length ? (
          role.capabilities.map((capability) => (
            <span
              key={capability}
              className="rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 text-[11px] font-medium text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]"
            >
              {formatCapability(capability)}
            </span>
          ))
        ) : (
          <span className="text-xs text-[var(--theme-text-tertiary)]">
            {t("roles.plaza.roleDirectory.noCapabilities")}
          </span>
        )}
      </div>

      {role.requestable && !canManageRoles ? (
        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={() => onRequestRole(role.role_id)}
            disabled={!canRequestRoles || requesting}
            className="btn-secondary h-9"
            title={
              canRequestRoles
                ? t("roles.plaza.actions.requestRole")
                : t("roles.plaza.actions.requestLocked")
            }
          >
            <GitPullRequestArrow size={15} />
            <span>
              {requesting
                ? t("roles.plaza.actions.queueing")
                : t("roles.plaza.actions.requestRole")}
            </span>
          </button>
        </div>
      ) : null}
    </article>
  );
}

export function RolesPanel() {
  const { t } = useTranslation();
  const {
    hasPermission,
    isAuthenticated,
    isLoading: authLoading,
  } = useAuth();
  const canRequestRoles = hasPermission(Permission.ROLE_REQUEST);
  const canManageRoles = hasPermission(Permission.ROLE_MANAGE);
  const [overview, setOverview] = useState<RoleGovernanceOverviewResponse | null>(
    null,
  );
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [operation, setOperation] = useState<OperationState>(null);

  const loadOverview = useCallback(async () => {
    if (!isAuthenticated && !authLoading) {
      setIsLoading(false);
      return;
    }
    if (authLoading) return;

    setIsLoading(true);
    setLoadError(null);
    try {
      const response = await roleGovernanceApi.getOverview("default");
      setOverview(response);
    } catch (err) {
      console.warn("[RolesPanel] Failed to load role governance overview:", err);
      setOverview(null);
      setLoadError(getRoleGovernanceLoadErrorMessage());
    } finally {
      setIsLoading(false);
    }
  }, [authLoading, isAuthenticated, t]);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  const roleGovernance = resolveRoleGovernanceState({
    isAuthenticated,
    isLoading: authLoading || isLoading,
    canManageRoles,
    canRequestRoles,
    overview,
    loadError,
  });
  const governanceRows = [
    {
      icon: UsersRound,
      title: t("roles.plaza.capabilities.roleDirectory.title"),
      description: t("roles.plaza.capabilities.roleDirectory.description"),
      availability: roleGovernance.capabilities.roleDirectory,
    },
    {
      icon: Building2,
      title: t("roles.plaza.capabilities.departmentScope.title"),
      description: t("roles.plaza.capabilities.departmentScope.description"),
      availability: roleGovernance.capabilities.departmentScope,
    },
    {
      icon: GitPullRequestArrow,
      title: t("roles.plaza.capabilities.requestFlow.title"),
      description: t("roles.plaza.capabilities.requestFlow.description"),
      availability: roleGovernance.capabilities.requestFlow,
    },
    {
      icon: History,
      title: t("roles.plaza.capabilities.auditTrail.title"),
      description: t("roles.plaza.capabilities.auditTrail.description"),
      availability: roleGovernance.capabilities.auditTrail,
    },
  ];
  const roles = overview?.role_directory.roles ?? [];
  const requestableDepartments = useMemo(
    () => (overview?.scope.departments ?? []).filter((department) => department.requestable),
    [overview?.scope.departments],
  );

  const runOperation = async (
    nextOperation: NonNullable<OperationState>,
    action: () => Promise<{ message: string }>,
  ) => {
    setOperation(nextOperation);
    try {
      const result = await action();
      toast.success(result.message);
      await loadOverview();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("roles.plaza.operationFailed"),
      );
    } finally {
      setOperation(null);
    }
  };

  const handleRequestRole = (roleId: string) => {
    void runOperation({ kind: "role", id: roleId }, () =>
      roleGovernanceApi.createRequest({
        target_type: "role",
        target_id: roleId,
        workspace_id: overview?.scope.workspace_id ?? "default",
        reason: t("roles.plaza.requestReasons.role", { role: roleId }),
      }),
    );
  };

  const handleRequestDepartment = (departmentId: string) => {
    void runOperation({ kind: "department", id: departmentId }, () =>
      roleGovernanceApi.createRequest({
        target_type: "department_agent",
        target_id: departmentId,
        workspace_id: overview?.scope.workspace_id ?? "default",
        reason: t("roles.plaza.requestReasons.department", {
          department: departmentId,
        }),
      }),
    );
  };

  const handleDecision = (
    request: RoleGovernanceRequestItem,
    operationKind: "approve" | "reject",
  ) => {
    void runOperation({ kind: operationKind, id: request.request_id }, () =>
      operationKind === "approve"
        ? roleGovernanceApi.approveRequest(request.request_id, {
            decision_note: t("roles.plaza.requestReasons.approve"),
          })
        : roleGovernanceApi.rejectRequest(request.request_id, {
            decision_note: t("roles.plaza.requestReasons.reject"),
          }),
    );
  };

  const handleRollback = (audit: RoleGovernanceAuditItem) => {
    void runOperation({ kind: "rollback", id: audit.audit_id }, () =>
      roleGovernanceApi.rollbackAudit(audit.audit_id, {
        reason: t("roles.plaza.requestReasons.rollback"),
      }),
    );
  };

  if (roleGovernance.pageState === "loading") {
    return (
      <div
        data-role-plaza-shell
        data-frontend-governance-state={roleGovernance.pageState}
        className={workbenchSurface.statePage}
      >
        <PanelLoadingState text={t("roles.plaza.loading")} />
      </div>
    );
  }

  if (roleGovernance.pageState === "logged-out" || roleGovernance.pageState === "forbidden") {
    return (
      <div
        data-role-plaza-shell
        data-frontend-governance-state={roleGovernance.pageState}
        className={workbenchSurface.statePage}
      >
        <WorkbenchStateSurface
          state={roleGovernance.pageState}
          surface="roles-governance"
          title={
            roleGovernance.pageState === "forbidden"
              ? t("roles.plaza.forbidden.title")
              : t("workbench.states.logged-out.title")
          }
          description={
            roleGovernance.pageState === "forbidden"
              ? t("roles.plaza.forbidden.description", {
                  permission: Permission.ROLE_READ,
                })
              : t("workbench.states.logged-out.description")
          }
          details={
            loadError ? [t("roles.plaza.degraded.detail")] : undefined
          }
        />
      </div>
    );
  }

  return (
    <div
      data-role-plaza-shell
      data-frontend-governance-state={roleGovernance.pageState}
      className={workbenchSurface.page}
    >
      <PanelHeader
        title={t("roles.plaza.title")}
        subtitle={t("roles.plaza.subtitle")}
        icon={<Shield size={22} className="text-theme-text-secondary" />}
        actions={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => void loadOverview()}
              disabled={isLoading}
              className="btn-secondary h-10"
              title={t("common.refresh")}
            >
              <RefreshCw
                size={16}
                className={isLoading ? "animate-spin" : undefined}
              />
              <span className="hidden sm:inline">{t("common.refresh")}</span>
            </button>
            <GovernanceAvailabilityBadge
              state={roleGovernance.roleDirectoryAvailability.state}
              labelKey={roleGovernance.roleDirectoryAvailability.labelKey}
            />
            <GovernanceAvailabilityBadge
              state={roleGovernance.adminAvailability.state}
              labelKey={roleGovernance.adminAvailability.labelKey}
            />
          </div>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-5 pt-3">
        <div className="grid gap-3">
          {roleGovernance.pageState === "degraded" ? (
            <WorkbenchStateSurface
              state="degraded"
              surface="roles-governance"
              title={t("roles.plaza.degraded.title")}
              description={t("roles.plaza.degraded.description")}
              details={
                loadError ? [t("roles.plaza.degraded.detail")] : undefined
              }
              className="max-w-none text-left"
            />
          ) : null}

          <section className="grid gap-3 lg:grid-cols-4">
            {governanceRows.map((row) => (
              <StatusTile
                key={row.title}
                icon={row.icon}
                title={row.title}
                description={row.description}
                state={row.availability.state}
                labelKey={row.availability.labelKey}
              />
            ))}
          </section>

          <section className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_24rem]">
            <div className={workbenchSurface.panel}>
              <div className="flex items-start justify-between gap-3 border-b border-[var(--theme-border)] px-4 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <UsersRound size={16} className="text-[var(--theme-text-secondary)]" />
                    <h2 className="text-sm font-semibold text-[var(--theme-text)]">
                      {t("roles.plaza.roleDirectory.title")}
                    </h2>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
                    {t("roles.plaza.roleDirectory.description", {
                      count: roles.length,
                    })}
                  </p>
                </div>
                <GovernanceAvailabilityBadge
                  state={roleGovernance.roleDirectoryAvailability.state}
                  labelKey={roleGovernance.roleDirectoryAvailability.labelKey}
                />
              </div>
              {roles.length ? (
                <div className="grid gap-3 p-4 md:grid-cols-2 2xl:grid-cols-3">
                  {roles.map((role) => (
                    <RoleCard
                      key={role.role_id}
                      role={role}
                      canRequestRoles={canRequestRoles}
                      canManageRoles={canManageRoles}
                      operation={operation}
                      onRequestRole={handleRequestRole}
                    />
                  ))}
                </div>
              ) : (
                <EmptyBlock>{t("roles.plaza.roleDirectory.empty")}</EmptyBlock>
              )}
            </div>

            <div className={workbenchSurface.panel}>
              <div className="border-b border-[var(--theme-border)] px-4 py-3">
                <div className="flex items-center gap-2">
                  <Building2 size={16} className="text-[var(--theme-text-secondary)]" />
                  <h2 className="text-sm font-semibold text-[var(--theme-text)]">
                    {t("roles.plaza.scope.title")}
                  </h2>
                </div>
                <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
                  {t("roles.plaza.scope.description", {
                    tenant: overview?.scope.tenant_id ?? "-",
                    workspace: overview?.scope.workspace_id ?? "-",
                  })}
                </p>
              </div>
              <div className="grid gap-3 p-4">
                {(overview?.scope.departments ?? []).map((department) => {
                  const requesting =
                    operation?.kind === "department" &&
                    operation.id === department.department_id;
                  return (
                    <div
                      key={department.department_id}
                      className="rounded-lg bg-[var(--theme-bg-sidebar)] p-3 ring-1 ring-[var(--theme-border)]"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                            {department.name}
                          </h3>
                          <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
                            {department.department_id}
                          </p>
                        </div>
                        <GovernanceAvailabilityBadge
                          state={
                            department.current_user_member
                              ? "enabled"
                              : department.requestable
                                ? "disabled"
                                : "unavailable"
                          }
                          labelKey={
                            department.current_user_member
                              ? "governance.enabled"
                              : department.requestable
                                ? "governance.disabled"
                                : "governance.unavailable"
                          }
                        />
                      </div>
                      {department.requestable && canRequestRoles ? (
                        <button
                          type="button"
                          onClick={() =>
                            handleRequestDepartment(department.department_id)
                          }
                          disabled={requesting}
                          className="btn-secondary mt-3 h-9"
                        >
                          <GitPullRequestArrow size={15} />
                          <span>
                            {requesting
                              ? t("roles.plaza.actions.queueing")
                              : t("roles.plaza.actions.requestDepartment")}
                          </span>
                        </button>
                      ) : null}
                    </div>
                  );
                })}
                {requestableDepartments.length === 0 && !overview?.scope.departments.length ? (
                  <EmptyBlock>{t("roles.plaza.scope.emptyDepartments")}</EmptyBlock>
                ) : null}
                <div className="rounded-lg bg-[var(--theme-bg-sidebar)] p-3 ring-1 ring-[var(--theme-border)]">
                  <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                    {t("roles.plaza.scope.skillsTitle")}
                  </h3>
                  <div className="mt-3 grid gap-2">
                    {(overview?.scope.skill_availability ?? []).length ? (
                      overview!.scope.skill_availability.map((skill) => (
                        <div
                          key={`${skill.skill_id}-${skill.scope_id}`}
                          className="flex items-center justify-between gap-3 text-xs"
                        >
                          <span className="min-w-0 truncate text-[var(--theme-text-secondary)]">
                            {skill.skill_id}
                          </span>
                          <span className="shrink-0 rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
                            {skill.availability_state} / {skill.inherited_from}
                          </span>
                        </div>
                      ))
                    ) : (
                      <p className="text-xs text-[var(--theme-text-secondary)]">
                        {t("roles.plaza.scope.emptySkills")}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </section>

          <section className="grid gap-3 xl:grid-cols-2">
            <div className={workbenchSurface.panel}>
              <div className="flex items-start justify-between gap-3 border-b border-[var(--theme-border)] px-4 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <GitPullRequestArrow size={16} className="text-[var(--theme-text-secondary)]" />
                    <h2 className="text-sm font-semibold text-[var(--theme-text)]">
                      {t("roles.plaza.requests.title")}
                    </h2>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
                    {t("roles.plaza.requests.description")}
                  </p>
                </div>
                <GovernanceAvailabilityBadge
                  state={roleGovernance.capabilities.requestFlow.state}
                  labelKey={roleGovernance.capabilities.requestFlow.labelKey}
                />
              </div>
              <div className="divide-y divide-[var(--theme-border)]">
                {(overview?.requests ?? []).length ? (
                  overview!.requests.map((request) => (
                    <article
                      key={request.request_id}
                      className="grid gap-3 p-4 text-sm lg:grid-cols-[minmax(0,1fr)_auto]"
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="font-semibold text-[var(--theme-text)]">
                            {request.target_type}: {request.target_id}
                          </h3>
                          <span className="rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 text-[11px] font-medium text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
                            {request.status}
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
                          {request.requester_id} · {formatTimestamp(request.created_at)}
                        </p>
                        {request.reason ? (
                          <p className="mt-2 line-clamp-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
                            {request.reason}
                          </p>
                        ) : null}
                      </div>
                      {canManageRoles ? (
                        <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                          <button
                            type="button"
                            onClick={() => handleDecision(request, "approve")}
                            disabled={
                              operation?.kind === "approve" &&
                              operation.id === request.request_id
                            }
                            className="btn-secondary h-9"
                          >
                            <CheckCircle2 size={15} />
                            <span>{t("roles.plaza.actions.approve")}</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDecision(request, "reject")}
                            disabled={
                              operation?.kind === "reject" &&
                              operation.id === request.request_id
                            }
                            className="btn-secondary h-9"
                          >
                            <LockKeyhole size={15} />
                            <span>{t("roles.plaza.actions.reject")}</span>
                          </button>
                        </div>
                      ) : null}
                    </article>
                  ))
                ) : (
                  <EmptyBlock>{t("roles.plaza.requests.empty")}</EmptyBlock>
                )}
              </div>
            </div>

            <div className={workbenchSurface.panel}>
              <div className="flex items-start justify-between gap-3 border-b border-[var(--theme-border)] px-4 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <History size={16} className="text-[var(--theme-text-secondary)]" />
                    <h2 className="text-sm font-semibold text-[var(--theme-text)]">
                      {t("roles.plaza.audit.title")}
                    </h2>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
                    {t("roles.plaza.audit.description")}
                  </p>
                </div>
                <GovernanceAvailabilityBadge
                  state={roleGovernance.capabilities.auditTrail.state}
                  labelKey={roleGovernance.capabilities.auditTrail.labelKey}
                />
              </div>
              <div className="divide-y divide-[var(--theme-border)]">
                {(overview?.audit ?? []).length ? (
                  overview!.audit.map((audit) => (
                    <article
                      key={audit.audit_id}
                      className="grid gap-3 p-4 text-sm lg:grid-cols-[minmax(0,1fr)_auto]"
                    >
                      <div className="min-w-0">
                        <h3 className="truncate font-semibold text-[var(--theme-text)]">
                          {audit.action}
                        </h3>
                        <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
                          {audit.actor_id} · {audit.target_type}:{audit.target_id}
                        </p>
                        <p className="mt-1 text-xs text-[var(--theme-text-tertiary)]">
                          {audit.audit_id} · {formatTimestamp(audit.created_at)}
                        </p>
                      </div>
                      {audit.rollback_available && canManageRoles ? (
                        <button
                          type="button"
                          onClick={() => handleRollback(audit)}
                          disabled={
                            operation?.kind === "rollback" &&
                            operation.id === audit.audit_id
                          }
                          className="btn-secondary h-9"
                        >
                          <RotateCcw size={15} />
                          <span>{t("roles.plaza.actions.rollback")}</span>
                        </button>
                      ) : null}
                    </article>
                  ))
                ) : (
                  <EmptyBlock>{t("roles.plaza.audit.empty")}</EmptyBlock>
                )}
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

export default RolesPanel;
