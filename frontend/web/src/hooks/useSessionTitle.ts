import { useEffect, useState } from "react";
import { sessionApi } from "../services/api";
import {
  getCachedSessionTitle,
  listenSessionTitleUpdated,
} from "../utils/sessionTitleEvents";

export function useSessionTitle(
  sessionId?: string | null,
  options?: { enabled?: boolean },
): string | null {
  const enabled = options?.enabled ?? true;
  const [sessionTitle, setSessionTitle] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !sessionId) {
      setSessionTitle(null);
      return;
    }

    let cancelled = false;
    const cachedTitle = getCachedSessionTitle(sessionId);
    if (cachedTitle) {
      setSessionTitle(cachedTitle);
    } else {
      setSessionTitle(null);
    }

    sessionApi
      .get(sessionId)
      .then((session) => {
        if (!cancelled && session?.name) {
          setSessionTitle(session.name);
        }
      })
      .catch((err) => {
        console.warn("[useSessionTitle] Failed to fetch session:", err);
      });

    const unsubscribe = listenSessionTitleUpdated((detail) => {
      if (detail.sessionId === sessionId) {
        setSessionTitle(detail.title);
      }
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [enabled, sessionId]);

  return sessionTitle;
}
