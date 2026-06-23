import type { FrontendGovernanceState } from "../../governance/frontendGovernanceState";
import type { GovernanceAvailabilityState } from "../../governance/groupAvailability";

export type TabType =
  | "chat"
  | "apps"
  | "skills"
  | "marketplace"
  | "users"
  | "roles"
  | "settings"
  | "mcp"
  | "feedback"
  | "channels"
  | "agents"
  | "models"
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
