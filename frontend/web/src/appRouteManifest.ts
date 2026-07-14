import { matchRoutes } from "react-router-dom";

export const APP_ROUTE_PATHS = {
  root: "/",
  login: "/auth/login",
  register: "/auth/register",
  chat: "/chat/:sessionId?",
  apps: "/apps",
  skills: "/skills",
  marketplace: "/marketplace",
  mcp: "/mcp",
  users: "/users",
  roles: "/roles",
  settings: "/settings",
  feedback: "/feedback",
  models: "/models",
  files: "/files",
  notifications: "/notifications",
  memory: "/memory",
  oauthCallback: "/auth/callback",
  resetRequest: "/auth/reset-request",
  resetPassword: "/auth/reset-password",
  verifyEmail: "/auth/verify-email",
  registrationPending: "/auth/pending",
  shared: "/shared/:shareId",
  notFound: "*",
} as const;

export type AppRouteId = keyof typeof APP_ROUTE_PATHS;

const APP_ROUTE_MANIFEST = Object.entries(APP_ROUTE_PATHS).map(
  ([id, path]) => ({ id: id as AppRouteId, path }),
);

/** Resolve a browser pathname through the same path manifest consumed by App. */
export function resolveAppRoute(pathname: string): AppRouteId {
  return (
    matchRoutes(APP_ROUTE_MANIFEST, pathname)?.at(-1)?.route.id ?? "notFound"
  );
}
