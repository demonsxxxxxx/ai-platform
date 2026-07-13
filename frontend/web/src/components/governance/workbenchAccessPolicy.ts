export type WorkbenchAccessUser = { is_admin?: boolean } | null;

export type WorkbenchAccessKey =
  | "chat"
  | "apps"
  | "skills"
  | "mcp"
  | "persona"
  | "files"
  | "agent-workspace"
  | "notifications"
  | "memory"
  | "users"
  | "roles"
  | "settings"
  | "channels"
  | "agents"
  | "models"
  | "feedback";

const ADMIN_ONLY_ITEMS = new Set<WorkbenchAccessKey>([
  "users",
  "roles",
  "settings",
  "channels",
  "agents",
  "models",
  "feedback",
]);

const PATH_ACCESS_KEYS: Array<[RegExp, WorkbenchAccessKey]> = [
  [/^\/chat(?:\/|$)/, "chat"],
  [/^\/apps(?:\/|$)/, "apps"],
  [/^\/skills(?:\/|$)/, "skills"],
  [/^\/marketplace(?:\/|$)/, "skills"],
  [/^\/mcp(?:\/|$)/, "mcp"],
  [/^\/persona(?:\/|$)/, "persona"],
  [/^\/files(?:\/|$)/, "files"],
  [/^\/agent-workspace(?:\/|$)/, "agent-workspace"],
  [/^\/notifications(?:\/|$)/, "notifications"],
  [/^\/memory(?:\/|$)/, "memory"],
  [/^\/users(?:\/|$)/, "users"],
  [/^\/roles(?:\/|$)/, "roles"],
  [/^\/settings(?:\/|$)/, "settings"],
  [/^\/channels(?:\/|$)/, "channels"],
  [/^\/agents(?:\/|$)/, "agents"],
  [/^\/models(?:\/|$)/, "models"],
  [/^\/feedback(?:\/|$)/, "feedback"],
];

/** Returns whether the signed user projection can enter a workbench surface. */
export function canAccessWorkbenchItem(
  user: WorkbenchAccessUser,
  item: WorkbenchAccessKey,
): boolean {
  return !ADMIN_ONLY_ITEMS.has(item) || user?.is_admin === true;
}

/** Applies the workbench policy to a browser pathname. */
export function canAccessWorkbenchPath(
  user: WorkbenchAccessUser,
  pathname: string,
): boolean {
  const normalizedPathname = pathname.startsWith("/")
    ? pathname
    : `/${pathname}`;
  const item = PATH_ACCESS_KEYS.find(([pattern]) =>
    pattern.test(normalizedPathname),
  )?.[1];
  return item ? canAccessWorkbenchItem(user, item) : true;
}

/** Returns the only role code exposed by the company-login product UI. */
export function getCanonicalCompanyRoleCode(
  user: WorkbenchAccessUser,
): "admin" | "user" {
  return user?.is_admin === true ? "admin" : "user";
}
