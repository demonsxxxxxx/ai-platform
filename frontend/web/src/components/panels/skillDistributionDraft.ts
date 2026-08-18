import type { CapabilityDistributionUpdate } from "../../services/api/capabilityDistribution";

export type DepartmentScopeMode = "all" | "restricted";

export function buildControlledSkillDistributionUpdate(
  draft: CapabilityDistributionUpdate,
  departmentScope: DepartmentScopeMode,
): CapabilityDistributionUpdate {
  return {
    ...draft,
    departmentIds:
      departmentScope === "all" ? [] : [...draft.departmentIds],
    allowedRoles: [...draft.allowedRoles],
    metadata: { ...draft.metadata },
  };
}
