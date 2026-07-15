import { clearAuthState } from "../services/api/tokenManager";
import { parseAuthStorageEvent } from "../services/api/token";

export function handleBrowserAuthStorageEvent(
  event: StorageEvent,
  refreshUser: () => void | Promise<unknown>,
): void {
  const authEvent = parseAuthStorageEvent(event);
  if (authEvent === "logout") {
    clearAuthState();
    return;
  }
  if (authEvent === "login") {
    void refreshUser();
  }
}
