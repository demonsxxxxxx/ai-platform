import {
  canAccessWorkbenchItem,
  type WorkbenchAccessUser,
} from "../../governance/workbenchAccessPolicy";

export type WorkbenchNavItem =
  | "apps"
  | "skills"
  | "persona"
  | "files"
  | "agent-workspace"
  | "mcp"
  | "channels"
  | "agents"
  | "models";

const routeToNavItem: Array<[RegExp, WorkbenchNavItem]> = [
  [/^\/apps(?:\/|$)/, "apps"],
  [/^\/skills(?:\/|$)/, "skills"],
  [/^\/persona(?:\/|$)/, "persona"],
  [/^\/files(?:\/|$)/, "files"],
  [/^\/agent-workspace(?:\/|$)/, "agent-workspace"],
  [/^\/mcp(?:\/|$)/, "mcp"],
  [/^\/channels(?:\/|$)/, "channels"],
  [/^\/agents(?:\/|$)/, "agents"],
  [/^\/models(?:\/|$)/, "models"],
];

const navItemToPath: Record<WorkbenchNavItem, string> = {
  apps: "/apps",
  skills: "/skills",
  persona: "/persona",
  files: "/files",
  "agent-workspace": "/agent-workspace",
  mcp: "/mcp",
  channels: "/channels",
  agents: "/agents",
  models: "/models",
};

/** Maps authenticated workbench pathnames to their first-level sidebar item. */
export function getWorkbenchNavItemFromPathname(
  pathname: string,
): WorkbenchNavItem | null {
  const normalizedPathname = pathname.startsWith("/") ? pathname : `/${pathname}`;
  return (
    routeToNavItem.find(([pattern]) => pattern.test(normalizedPathname))?.[1] ??
    null
  );
}

/** Returns the authenticated workbench route for a first-level sidebar item. */
export function getWorkbenchNavPath(item: WorkbenchNavItem): string {
  return navItemToPath[item];
}

/** Returns a destination that cannot enter an unauthorized workbench route. */
export function getSafeWorkbenchNavPath(
  item: WorkbenchNavItem,
  user: WorkbenchAccessUser,
): string {
  return canAccessWorkbenchItem(user, item) ? navItemToPath[item] : "/chat";
}
