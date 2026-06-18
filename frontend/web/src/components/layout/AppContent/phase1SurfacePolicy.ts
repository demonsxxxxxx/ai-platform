import { Permission } from "../../../types/auth";
import type { TabType } from "./types";

export type Phase1SurfaceClassification =
  | "reuse-current"
  | "remap-current"
  | "fail-closed-placeholder"
  | "phase-2-backend";

export type Phase1SurfaceRender =
  | "none"
  | "skills"
  | "files"
  | "persona"
  | "feedback"
  | "memory"
  | "agents"
  | "mcp"
  | "models"
  | "notifications"
  | "admin-runtime"
  | "phase2-unavailable";

export interface Phase1SurfacePolicy {
  tab: TabType;
  classification: Phase1SurfaceClassification;
  render: Phase1SurfaceRender;
  routePermissions: Permission[];
  navPermissions: Permission[];
  requiresFeature?: "memory" | "skills";
}

export const PHASE_2_TABS = [
  "marketplace",
  "users",
  "roles",
  "feedback",
  "channels",
  "files",
  "persona",
] as const satisfies readonly TabType[];

const POLICIES: Record<TabType, Phase1SurfacePolicy> = {
  chat: {
    tab: "chat",
    classification: "reuse-current",
    render: "none",
    routePermissions: [],
    navPermissions: [],
  },
  skills: {
    tab: "skills",
    classification: "remap-current",
    render: "skills",
    routePermissions: [Permission.AGENT_ADMIN],
    navPermissions: [Permission.AGENT_ADMIN],
    requiresFeature: "skills",
  },
  marketplace: {
    tab: "marketplace",
    classification: "phase-2-backend",
    render: "phase2-unavailable",
    routePermissions: [Permission.ADMIN_STATUS],
    navPermissions: [],
    requiresFeature: "skills",
  },
  users: {
    tab: "users",
    classification: "phase-2-backend",
    render: "phase2-unavailable",
    routePermissions: [Permission.ADMIN_STATUS],
    navPermissions: [],
  },
  roles: {
    tab: "roles",
    classification: "phase-2-backend",
    render: "phase2-unavailable",
    routePermissions: [Permission.ADMIN_STATUS],
    navPermissions: [],
  },
  settings: {
    tab: "settings",
    classification: "remap-current",
    render: "admin-runtime",
    routePermissions: [Permission.ADMIN_STATUS, Permission.SETTINGS_MANAGE],
    navPermissions: [Permission.ADMIN_STATUS, Permission.SETTINGS_MANAGE],
  },
  mcp: {
    tab: "mcp",
    classification: "remap-current",
    render: "mcp",
    routePermissions: [Permission.ADMIN_STATUS],
    navPermissions: [Permission.ADMIN_STATUS],
  },
  feedback: {
    tab: "feedback",
    classification: "phase-2-backend",
    render: "phase2-unavailable",
    routePermissions: [Permission.ADMIN_STATUS],
    navPermissions: [],
  },
  channels: {
    tab: "channels",
    classification: "phase-2-backend",
    render: "phase2-unavailable",
    routePermissions: [Permission.ADMIN_STATUS],
    navPermissions: [],
  },
  agents: {
    tab: "agents",
    classification: "remap-current",
    render: "agents",
    routePermissions: [Permission.AGENT_ADMIN],
    navPermissions: [Permission.AGENT_ADMIN],
  },
  models: {
    tab: "models",
    classification: "remap-current",
    render: "models",
    routePermissions: [Permission.MODEL_ADMIN],
    navPermissions: [Permission.MODEL_ADMIN],
  },
  files: {
    tab: "files",
    classification: "phase-2-backend",
    render: "phase2-unavailable",
    routePermissions: [Permission.ADMIN_STATUS],
    navPermissions: [],
  },
  persona: {
    tab: "persona",
    classification: "phase-2-backend",
    render: "phase2-unavailable",
    routePermissions: [Permission.ADMIN_STATUS],
    navPermissions: [],
  },
  notifications: {
    tab: "notifications",
    classification: "remap-current",
    render: "notifications",
    routePermissions: [Permission.ADMIN_STATUS],
    navPermissions: [Permission.ADMIN_STATUS],
  },
  memory: {
    tab: "memory",
    classification: "reuse-current",
    render: "memory",
    routePermissions: [Permission.CHAT_READ, Permission.SESSION_READ],
    navPermissions: [Permission.CHAT_READ, Permission.SESSION_READ],
    requiresFeature: "memory",
  },
};

function hasAnyPermission(
  ownedPermissions: readonly Permission[],
  requiredPermissions: readonly Permission[],
): boolean {
  return (
    requiredPermissions.length === 0 ||
    requiredPermissions.some((permission) => ownedPermissions.includes(permission))
  );
}

export function getSurfacePolicy(tab: TabType): Phase1SurfacePolicy {
  return POLICIES[tab];
}

export function getRoutePermissions(tab: TabType): Permission[] {
  return getSurfacePolicy(tab).routePermissions;
}

export function canShowSurfaceInNavigation(
  tab: TabType,
  permissions: readonly Permission[],
  featureEnabled = true,
): boolean {
  const policy = getSurfacePolicy(tab);
  if (!featureEnabled && policy.requiresFeature) return false;
  if (policy.navPermissions.length === 0) return false;
  return hasAnyPermission(permissions, policy.navPermissions);
}

export function isPhase2Surface(tab: TabType): boolean {
  return getSurfacePolicy(tab).classification === "phase-2-backend";
}
