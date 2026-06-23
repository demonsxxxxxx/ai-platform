import type { FrontendGovernanceState } from "../../governance/frontendGovernanceState";

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
  | "files"
  | "notifications"
  | "memory";

export interface RouteUnavailableConfig {
  state: FrontendGovernanceState;
  title: string;
  description: string;
  surface: string;
}
