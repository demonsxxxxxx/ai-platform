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
  state: "forbidden" | "no-workspace" | "degraded";
  title: string;
  description: string;
  surface: string;
}
