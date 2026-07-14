import type { FrontendGovernanceState } from "../../governance/frontendGovernanceState";
import type { GovernanceAvailabilityState } from "../../governance/groupAvailability";

export type TabType =
  | "chat"
  | "apps"
  | "skills"
  | "users"
  | "roles"
  | "settings"
  | "mcp"
  | "feedback"
  | "models"
  | "files"
  | "notifications"
  | "memory";

export interface RouteUnavailableConfig {
  state: FrontendGovernanceState;
  title: string;
  description: string;
  surface: string;
  details?: string[];
  capabilities?: Array<{
    title: string;
    description: string;
    state: GovernanceAvailabilityState;
    labelKey?: string;
  }>;
}
