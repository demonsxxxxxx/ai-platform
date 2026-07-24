export const SESSION_CONFIG_STORAGE_KEY = "ai-platform-session-config";
const LEGACY_SESSION_CONFIG_STORAGE_KEY = "lambchat_session_config";

export function readSessionConfigStorage(): string | null {
  const raw =
    localStorage.getItem(SESSION_CONFIG_STORAGE_KEY) ??
    localStorage.getItem(LEGACY_SESSION_CONFIG_STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return JSON.stringify({
      disabledSkills: Array.isArray(parsed.disabledSkills)
        ? parsed.disabledSkills
        : [],
    });
  } catch {
    return null;
  }
}

export function writeSessionConfigStorage(value: string): void {
  let disabledSkills: unknown[] = [];
  try {
    const parsed = JSON.parse(value);
    if (Array.isArray(parsed.disabledSkills)) {
      disabledSkills = parsed.disabledSkills;
    }
  } catch {
    // Persist a valid empty preference envelope for malformed callers.
  }
  localStorage.setItem(
    SESSION_CONFIG_STORAGE_KEY,
    JSON.stringify({ disabledSkills }),
  );
}
