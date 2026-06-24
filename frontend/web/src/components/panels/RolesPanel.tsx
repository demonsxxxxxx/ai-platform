import {
  Building2,
  GitPullRequestArrow,
  History,
  LockKeyhole,
  Shield,
  UsersRound,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { PanelHeader } from "../common/PanelHeader";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import { resolveRoleGovernanceState } from "./roleGovernanceState";

function CapabilityRow({
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
    <div className="grid gap-3 border-b border-[var(--theme-border)] p-3 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_8rem] sm:items-start">
      <div className="flex min-w-0 items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] text-slate-500 ring-1 ring-[var(--theme-border)] dark:bg-stone-950 dark:text-stone-300 dark:ring-stone-800">
          <Icon size={17} />
        </div>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-stone-100">
            {title}
          </h3>
          <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">
            {description}
          </p>
        </div>
      </div>
      <div className="sm:justify-self-end">
        <GovernanceAvailabilityBadge state={state} labelKey={labelKey} />
      </div>
    </div>
  );
}

export function RolesPanel() {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const canManageRoles = hasPermission(Permission.ROLE_MANAGE);
  const roleGovernance = resolveRoleGovernanceState({
    canManageRoles,
    roleDirectoryBacked: false,
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

  return (
    <div
      data-role-plaza-shell
      data-frontend-governance-state={roleGovernance.pageState}
      className="flex h-full min-h-0 flex-col bg-[var(--theme-bg)] text-slate-950 dark:bg-stone-950 dark:text-stone-100"
    >
      <PanelHeader
        title={t("roles.plaza.title")}
        subtitle={t("roles.plaza.subtitle")}
        icon={<Shield size={22} className="text-theme-text-secondary" />}
        actions={
          <div className="flex flex-wrap items-center justify-end gap-2">
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
          <div className="grid min-w-0 gap-3">
            <WorkbenchStateSurface
              state={roleGovernance.pageState}
              surface="roles-public-catalog"
              title={t("roles.plaza.state.title")}
              description={t("roles.plaza.state.description")}
              details={[
                t("roles.plaza.state.details.loginReachable"),
                t("roles.plaza.state.details.failClosed"),
                t("roles.plaza.state.details.requiredPermission", {
                  permission: Permission.ROLE_MANAGE,
                }),
              ]}
              className="max-w-none text-left"
            />
            <section className={workbenchSurface.compactPanel}>
              <div className="flex items-start justify-between gap-3 border-b border-[var(--theme-border)] p-3">
                <div className="min-w-0">
                  <h2 className="text-sm font-semibold text-slate-900 dark:text-stone-100">
                    {t("roles.plaza.capabilityMatrixTitle")}
                  </h2>
                  <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">
                    {t("roles.plaza.capabilityMatrixDescription")}
                  </p>
                </div>
                <LockKeyhole
                  size={18}
                  className="mt-0.5 shrink-0 text-slate-400 dark:text-stone-500"
                />
              </div>
              {governanceRows.map((row) => (
                <CapabilityRow
                  key={row.title}
                  icon={row.icon}
                  title={row.title}
                  description={row.description}
                  state={row.availability.state}
                  labelKey={row.availability.labelKey}
                />
              ))}
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}

export default RolesPanel;
