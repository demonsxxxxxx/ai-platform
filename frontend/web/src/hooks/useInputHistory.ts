import { useCallback, useRef, useSyncExternalStore } from "react";
import { registerAuthScopedCacheClearer } from "../services/api/authCacheInvalidation";
import type { User } from "../types";

const MAX_HISTORY = 200;
const LEGACY_STORAGE_KEY = "chatInputHistory";
const STORAGE_PREFIX = "chatInputHistory:v2:";
const EMPTY_HISTORY: string[] = [];
const histories = new Map<string, string[]>();
const listeners = new Set<() => void>();
let authGeneration = 0;

type InputHistoryOwner = Pick<User, "id" | "tenant_id"> | null;

function storageKeyForOwner(owner: InputHistoryOwner): string | null {
  // An incomplete principal cannot claim previously stored prompts.
  return owner?.id && owner.tenant_id
    ? `${STORAGE_PREFIX}${JSON.stringify([owner.tenant_id, owner.id])}`
    : null;
}

function discardLegacyHistory() {
  try {
    // The global key has no trustworthy owner. Never migrate its contents.
    localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch { /* storage unavailable */ }
}

function readHistory(key: string | null): string[] {
  discardLegacyHistory();
  if (!key) return EMPTY_HISTORY;
  const cached = histories.get(key);
  if (cached) return cached;
  let history = EMPTY_HISTORY;
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(key) ?? "null");
    if (Array.isArray(stored)) {
      history = stored.filter((value): value is string => typeof value === "string")
        .slice(-MAX_HISTORY);
    }
  } catch { /* invalid or unavailable storage */ }
  histories.set(key, history);
  return history;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

registerAuthScopedCacheClearer(() => {
  authGeneration += 1;
  discardLegacyHistory();
  try {
    const keys = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index));
    for (const key of keys) {
      if (key?.startsWith(STORAGE_PREFIX)) {
        histories.set(key, EMPTY_HISTORY);
        localStorage.removeItem(key);
      }
    }
  } catch { /* storage unavailable */ }
  // Retain empty entries so failed storage removal cannot reload known prompts.
  for (const key of histories.keys()) histories.set(key, EMPTY_HISTORY);
  for (const listener of listeners) listener();
});

export function useInputHistory(owner: InputHistoryOwner) {
  const key = storageKeyForOwner(owner);
  const generation = authGeneration;
  const getSnapshot = useCallback(() => readHistory(key), [key]);
  const history = useSyncExternalStore(subscribe, getSnapshot, () => EMPTY_HISTORY);
  const navigationRef = useRef({ key, generation, index: -1, draft: "" });
  if (navigationRef.current.key !== key || navigationRef.current.generation !== generation) {
    navigationRef.current = { key, generation, index: -1, draft: "" };
  }
  const navigationOwner = navigationRef.current;
  const isCurrentOwner = () => key !== null
    && navigationRef.current === navigationOwner && generation === authGeneration;

  const resetIndex = () => {
    if (!isCurrentOwner()) return;
    navigationRef.current.index = -1;
    navigationRef.current.draft = "";
  };

  const pushHistory = (value: string) => {
    if (!isCurrentOwner() || !key) return;
    const trimmed = value.trim();
    if (!trimmed) return;
    const next = [...readHistory(key), trimmed].slice(-MAX_HISTORY);
    histories.set(key, next);
    try {
      localStorage.setItem(key, JSON.stringify(next));
    } catch { /* storage full or unavailable */ }
    resetIndex();
    for (const listener of listeners) listener();
  };

  const navigateUp = (currentInput: string) => {
    if (!isCurrentOwner() || history.length === 0) return null;
    const navigation = navigationRef.current;
    if (navigation.index === -1) navigation.draft = currentInput;
    navigation.index = Math.min(navigation.index + 1, history.length - 1);
    return history[history.length - 1 - navigation.index];
  };

  const navigateDown = () => {
    if (!isCurrentOwner()) return null;
    const navigation = navigationRef.current;
    if (navigation.index === -1) return null;
    navigation.index -= 1;
    return navigation.index < 0
      ? navigation.draft
      : history[history.length - 1 - navigation.index];
  };

  return {
    history,
    pushHistory,
    resetIndex,
    navigateUp,
    navigateDown,
    isBrowsing: navigationRef.current.index !== -1,
  };
}
