import { useState, useRef } from "react";

const MAX_HISTORY = 200;
const STORAGE_KEY = "chatInputHistory";

export function useInputHistory() {
  const [history, setHistory] = useState<string[]>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored) : [];
    } catch {
      return [];
    }
  });
  const historyIndexRef = useRef(-1);
  const draftRef = useRef("");

  const pushHistory = (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return;
    setHistory((prev) => {
      const next = [...prev, trimmed].slice(-MAX_HISTORY);
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        /* storage full or unavailable */
      }
      return next;
    });
    historyIndexRef.current = -1;
    draftRef.current = "";
  };

  const resetIndex = () => {
    historyIndexRef.current = -1;
    draftRef.current = "";
  };

  const navigateUp = (currentInput: string) => {
    if (history.length === 0) return null;
    if (historyIndexRef.current === -1) {
      draftRef.current = currentInput;
    }
    const newIndex = Math.min(historyIndexRef.current + 1, history.length - 1);
    historyIndexRef.current = newIndex;
    return history[history.length - 1 - newIndex];
  };

  const navigateDown = () => {
    if (historyIndexRef.current === -1) return null;
    const newIndex = historyIndexRef.current - 1;
    if (newIndex < 0) {
      historyIndexRef.current = -1;
      return draftRef.current;
    }
    historyIndexRef.current = newIndex;
    return history[history.length - 1 - newIndex];
  };

  const isBrowsing = historyIndexRef.current !== -1;

  return {
    history,
    pushHistory,
    resetIndex,
    navigateUp,
    navigateDown,
    isBrowsing,
  };
}
