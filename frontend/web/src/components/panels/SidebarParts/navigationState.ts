import {
  canAccessWorkbenchItem,
  type WorkbenchAccessUser,
} from "../../governance/workbenchAccessPolicy";

export type WorkbenchNavItem =
  | "apps"
  | "agentBuilder"
  | "skills"
  | "files"
  | "mcp"
  | "models";

const routeToNavItem: Array<[RegExp, WorkbenchNavItem]> = [
  [/^\/apps(?:\/|$)/, "apps"],
  [/^\/agent-builder(?:\/|$)/, "agentBuilder"],
  [/^\/agent-market(?:\/|$)/, "agentBuilder"],
  [/^\/skills(?:\/|$)/, "skills"],
  [/^\/files(?:\/|$)/, "files"],
  [/^\/mcp(?:\/|$)/, "mcp"],
  [/^\/models(?:\/|$)/, "models"],
];

const navItemToPath: Record<WorkbenchNavItem, string> = {
  apps: "/apps",
  agentBuilder: "/agent-builder",
  skills: "/skills",
  files: "/files",
  mcp: "/mcp",
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
  if (item === "agentBuilder") {
    return user?.is_admin === true ? "/agent-builder" : "/agent-market";
  }
  return canAccessWorkbenchItem(user, item) ? navItemToPath[item] : "/chat";
}
