import { parseAuthStorageEvent } from "../services/api/token";

export type BrowserAuthStorageChange =
  | { type: "login" | "replacement"; marker: string }
  | { type: "logout" };

/** Classify a cross-tab marker change without mutating shared auth state. */
export function classifyBrowserAuthStorageEvent(
  event: StorageEvent,
): BrowserAuthStorageChange | null {
  const authEvent = parseAuthStorageEvent(event);
  if (authEvent === "logout") {
    return { type: "logout" };
  }
  if (
    (authEvent === "login" || authEvent === "replacement") &&
    event.newValue
  ) {
    return { type: authEvent, marker: event.newValue };
  }
  return null;
}
