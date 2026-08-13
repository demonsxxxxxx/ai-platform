import type { ReactNode } from "react";
import { Navigate, useParams } from "react-router-dom";

import { APP_ROUTE_PATHS } from "../../appRouteManifest";

/** Keep historical Chat sessions readable without exposing bare generic Chat. */
export function ChatRouteBoundary({ children }: { children: ReactNode }) {
  const { sessionId } = useParams<{ sessionId?: string }>();
  return sessionId ? children : <Navigate to={APP_ROUTE_PATHS.agentMarket} replace />;
}
