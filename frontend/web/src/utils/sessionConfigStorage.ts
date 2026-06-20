export const SESSION_CONFIG_STORAGE_KEY = "ai-platform-session-config";
const LEGACY_SESSION_CONFIG_STORAGE_KEY = "lambchat_session_config";

export function readSessionConfigStorage(): string | null {
  return (
    localStorage.getItem(SESSION_CONFIG_STORAGE_KEY) ??
    localStorage.getItem(LEGACY_SESSION_CONFIG_STORAGE_KEY)
  );
}

export function writeSessionConfigStorage(value: string): void {
  localStorage.setItem(SESSION_CONFIG_STORAGE_KEY, value);
}
