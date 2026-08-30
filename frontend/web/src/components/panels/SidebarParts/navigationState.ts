import {
  canAccessWorkbenchItem,
  type WorkbenchAccessUser,
} from "../../governance/workbenchAccessPolicy";

export type WorkbenchNavItem =
  | "apps"
  | "agentMarket"
  | "agentBuilder"
  | "skills"
  | "mcp"
  | "knowledge"
  | "models"
  | "runs";

const routeToNavItem: Array<[RegExp, WorkbenchNavItem]> = [
  [/^\/apps(?:\/|$)/, "apps"],
  [/^\/agent-builder(?:\/|$)/, "agentBuilder"],
  [/^\/agent-market(?:\/|$)/, "agentMarket"],
  [/^\/skills(?:\/|$)/, "skills"],
  [/^\/mcp(?:\/|$)/, "mcp"],
  [/^\/knowledge(?:\/|$)/, "knowledge"],
  [/^\/models(?:\/|$)/, "models"],
  [/^\/runs(?:\/|$)/, "runs"],
];

const navItemToPath: Record<WorkbenchNavItem, string> = {
  apps: "/apps",
  agentMarket: "/agent-market",
  agentBuilder: "/agent-builder",
  skills: "/skills",
  mcp: "/mcp",
  knowledge: "/knowledge",
  models: "/models",
  runs: "/runs",
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
  if (item === "agentMarket") {
    return "/agent-market";
  }
  if (item === "agentBuilder") {
    return user?.is_admin === true ? "/agent-builder" : "/agent-market";
  }
  return canAccessWorkbenchItem(user, item)
    ? navItemToPath[item]
    : "/agent-market";
}
