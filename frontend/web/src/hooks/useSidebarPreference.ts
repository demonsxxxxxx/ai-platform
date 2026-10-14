import { useCallback, useRef, useState, type Dispatch, type SetStateAction } from "react";

/** Browser-local presentation preference; it is not a user profile fact. */
export const SIDEBAR_COLLAPSED_STORAGE_KEY = "ai-platform-sidebar-collapsed";

export function useSidebarPreference(): [boolean, Dispatch<SetStateAction<boolean>>] {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === "true";
    } catch {
      return false;
    }
  });
  const current = useRef(collapsed);
  const setPreference = useCallback<Dispatch<SetStateAction<boolean>>>((value) => {
    const next = typeof value === "function" ? value(current.current) : value;
    current.current = next;
    setCollapsed(next);
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(next));
    } catch {
      // Keep this tab usable when persistent browser storage is unavailable.
    }
  }, []);
  return [collapsed, setPreference];
}
