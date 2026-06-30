export type WorkbenchNavItem =
  | "apps"
  | "skills"
  | "marketplace"
  | "persona"
  | "files"
  | "mcp"
  | "channels"
  | "agents"
  | "models"
  | "roles";

const routeToNavItem: Array<[RegExp, WorkbenchNavItem]> = [
  [/^\/apps(?:\/|$)/, "apps"],
  [/^\/skills(?:\/|$)/, "skills"],
  [/^\/marketplace(?:\/|$)/, "marketplace"],
  [/^\/persona(?:\/|$)/, "persona"],
  [/^\/files(?:\/|$)/, "files"],
  [/^\/mcp(?:\/|$)/, "mcp"],
  [/^\/channels(?:\/|$)/, "channels"],
  [/^\/agents(?:\/|$)/, "agents"],
  [/^\/models(?:\/|$)/, "models"],
  [/^\/roles(?:\/|$)/, "roles"],
];

const navItemToPath: Record<WorkbenchNavItem, string> = {
  apps: "/apps",
  skills: "/skills",
  marketplace: "/marketplace",
  persona: "/persona",
  files: "/files",
  mcp: "/mcp",
  channels: "/channels",
  agents: "/agents",
  models: "/models",
  roles: "/roles",
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
