import type { BackendSession } from "../../services/api/session";

export function isSessionFavorite(session: BackendSession): boolean {
  return session.metadata?.is_favorite === true;
}
