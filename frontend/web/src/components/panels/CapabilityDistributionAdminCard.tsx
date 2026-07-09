import { useEffect, useState } from "react";
import type {
  CapabilityDistribution,
  CapabilityDistributionStatus,
  CapabilityDistributionUpdateRequest,
  CapabilityKind,
} from "../../types";

function splitScopeInput(value: string): string[] {
  const seen = new Set<string>();
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item) => {
      if (seen.has(item)) {
        return false;
      }
      seen.add(item);
      return true;
    });
}

function joinScopeInput(values: string[] | undefined): string {
  return (values ?? []).join(", ");
}

function scopeSummary(values: string[], emptyLabel: string): string {
  return values.length > 0 ? values.join(", ") : emptyLabel;
}

function scopePillClass(enabled: boolean): string {
  return enabled
    ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
    : "bg-stone-200 text-stone-700 dark:bg-stone-800 dark:text-stone-200";
}

interface CapabilityDistributionAdminCardProps {
  capabilityKind: CapabilityKind;
  capabilityId: string;
  distribution: CapabilityDistribution | null;
  fallbackStatus?: CapabilityDistributionStatus;
  fallbackVisibleToUser?: boolean;
  fallbackDepartmentIds?: string[];
  fallbackAllowedRoles?: string[];
  isBusy?: boolean;
  onSave: (payload: CapabilityDistributionUpdateRequest) => Promise<boolean>;
  onToggle: (
    enabled: boolean,
    fallbackPayload: CapabilityDistributionUpdateRequest,
  ) => Promise<boolean>;
}

export function CapabilityDistributionAdminCard({
  capabilityKind,
  capabilityId,
  distribution,
  fallbackStatus = "disabled",
  fallbackVisibleToUser = false,
  fallbackDepartmentIds = [],
  fallbackAllowedRoles = [],
  isBusy = false,
  onSave,
  onToggle,
}: CapabilityDistributionAdminCardProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [status, setStatus] =
    useState<CapabilityDistributionStatus>(fallbackStatus);
  const [visibleToUser, setVisibleToUser] = useState(fallbackVisibleToUser);
  const [departmentInput, setDepartmentInput] = useState(
    joinScopeInput(fallbackDepartmentIds),
  );
  const [roleInput, setRoleInput] = useState(joinScopeInput(fallbackAllowedRoles));

  useEffect(() => {
    setStatus(distribution?.status ?? fallbackStatus);
    setVisibleToUser(distribution?.visible_to_user ?? fallbackVisibleToUser);
    setDepartmentInput(
      joinScopeInput(distribution?.department_ids ?? fallbackDepartmentIds),
    );
    setRoleInput(joinScopeInput(distribution?.allowed_roles ?? fallbackAllowedRoles));
  }, [
    distribution,
    fallbackAllowedRoles,
    fallbackDepartmentIds,
    fallbackStatus,
    fallbackVisibleToUser,
  ]);

  const departmentIds = splitScopeInput(departmentInput);
  const allowedRoles = splitScopeInput(roleInput);
  const nextPayload: CapabilityDistributionUpdateRequest = {
    status,
    visible_to_user: visibleToUser,
    scope_mode: "allowlist",
    department_ids: departmentIds,
    allowed_roles: allowedRoles,
    metadata_json: distribution?.metadata_json ?? {},
  };

  const handleReset = () => {
    setStatus(distribution?.status ?? fallbackStatus);
    setVisibleToUser(distribution?.visible_to_user ?? fallbackVisibleToUser);
    setDepartmentInput(
      joinScopeInput(distribution?.department_ids ?? fallbackDepartmentIds),
    );
    setRoleInput(joinScopeInput(distribution?.allowed_roles ?? fallbackAllowedRoles));
  };

  const handleSave = async () => {
    const saved = await onSave(nextPayload);
    if (saved) {
      setIsOpen(false);
    }
  };

  const handleToggle = async () => {
    const nextStatus = status === "active" ? "disabled" : "active";
    const toggled = await onToggle(nextStatus === "active", {
      ...nextPayload,
      status: nextStatus,
    });
    if (toggled) {
      setStatus(nextStatus);
    }
  };

  return (
    <div
      data-capability-distribution-admin
      data-capability-kind={capabilityKind}
      data-capability-id={capabilityId}
      className="mt-3 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-3"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--theme-text-secondary)]">
            Department Distribution
          </p>
          <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
            {scopeSummary(departmentIds, "Tenant-wide")} ·{" "}
            {scopeSummary(allowedRoles, "All roles")}
          </p>
          <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
            {visibleToUser ? "Visible in user catalogs" : "Hidden from ordinary users"}
          </p>
          {distribution === null ? (
            <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
              No unified row exists yet. Saving will create one.
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`rounded-full px-2 py-1 text-[11px] font-medium ${scopePillClass(
              status === "active",
            )}`}
          >
            {status === "active" ? "Active" : "Disabled"}
          </span>
          <button
            type="button"
            onClick={handleToggle}
            disabled={isBusy}
            className="btn-secondary h-8 px-2 text-xs disabled:opacity-50"
          >
            {status === "active" ? "Disable" : "Enable"}
          </button>
          <button
            type="button"
            onClick={() => setIsOpen((current) => !current)}
            className="btn-secondary h-8 px-2 text-xs"
          >
            {isOpen ? "Hide scope" : "Edit scope"}
          </button>
        </div>
      </div>

      {isOpen ? (
        <div className="mt-3 grid gap-3 border-t border-[var(--theme-border)] pt-3">
          <label className="grid gap-1">
            <span className="text-xs font-medium text-[var(--theme-text)]">
              Departments
            </span>
            <input
              type="text"
              value={departmentInput}
              onChange={(event) => setDepartmentInput(event.target.value)}
              placeholder="qa, rd"
              className="enterprise-field-control es-input px-3"
            />
            <span className="text-xs text-[var(--theme-text-secondary)]">
              Leave blank for tenant-wide distribution.
            </span>
          </label>

          <label className="grid gap-1">
            <span className="text-xs font-medium text-[var(--theme-text)]">
              Roles
            </span>
            <input
              type="text"
              value={roleInput}
              onChange={(event) => setRoleInput(event.target.value)}
              placeholder="qa_operator, reviewer"
              className="enterprise-field-control es-input px-3"
            />
            <span className="text-xs text-[var(--theme-text-secondary)]">
              Leave blank to allow every role inside the chosen departments.
            </span>
          </label>

          <label className="flex items-center gap-2 rounded-md border border-dashed border-[var(--theme-border)] px-3 py-2 text-sm text-[var(--theme-text)]">
            <input
              type="checkbox"
              checked={visibleToUser}
              onChange={(event) => setVisibleToUser(event.target.checked)}
            />
            <span>Visible to ordinary users in catalog and composer</span>
          </label>

          <div className="flex flex-wrap justify-end gap-2">
            <button
              type="button"
              onClick={handleReset}
              className="btn-secondary h-9 px-3 text-xs"
            >
              Reset
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={isBusy}
              className="btn-primary h-9 px-3 text-xs disabled:opacity-50"
            >
              {isBusy ? "Saving..." : "Save scope"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
