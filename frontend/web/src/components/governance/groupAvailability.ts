export type GovernanceAvailabilityState =
  | "enabled"
  | "disabled"
  | "inherited"
  | "admin-only"
  | "unavailable";

export interface GroupAvailabilityInput {
  backed?: boolean;
  enabled?: boolean;
  inherited?: boolean;
  adminOnly?: boolean;
}

export interface GroupAvailabilityResult {
  state: GovernanceAvailabilityState;
  labelKey: string;
}

export function resolveGroupAvailability(
  input: GroupAvailabilityInput,
): GroupAvailabilityResult {
  if (input.backed === false) {
    return { state: "unavailable", labelKey: "governance.unavailable" };
  }
  if (input.adminOnly) {
    return { state: "admin-only", labelKey: "governance.adminOnly" };
  }
  if (input.inherited) {
    return { state: "inherited", labelKey: "governance.inherited" };
  }
  if (input.enabled === true) {
    return { state: "enabled", labelKey: "governance.enabled" };
  }
  return { state: "disabled", labelKey: "governance.disabled" };
}
