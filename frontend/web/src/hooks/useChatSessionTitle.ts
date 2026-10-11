import { useEffect, useRef, useState } from "react";
import { sessionApi } from "../services/api/session";
import { listenSessionTitleUpdated } from "../utils/sessionTitleEvents";

interface ChatTitleScope {
  sessionId?: string;
  tenantId?: string;
  userId?: string;
}

/** A title is a projection of an authorized session read, not a global cache. */
export function useChatSessionTitle({ sessionId, tenantId, userId }: ChatTitleScope): string | null {
  const scope = sessionId && tenantId && userId
    ? JSON.stringify([tenantId, userId, sessionId])
    : null;
  const activeScope = useRef({ scope, generation: 0 });
  if (activeScope.current.scope !== scope) {
    activeScope.current = { scope, generation: activeScope.current.generation + 1 };
  }
  const generation = activeScope.current.generation;
  const [snapshot, setSnapshot] = useState<{ generation: number; title: string | null } | null>(null);

  useEffect(() => {
    if (!scope || !sessionId) return;
    let active = true;
    let requestSequence = 0;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const ownsScope = () => active && activeScope.current.generation === generation;
    const publish = (title: string | null) => {
      if (ownsScope()) setSnapshot({ generation, title });
    };
    // Each scope incarnation starts empty; returning to an earlier subject or
    // session must not resurrect its previously authorized projection.
    publish(null);
    const load = async (mayPoll: boolean) => {
      const request = ++requestSequence;
      const ownsRequest = () => ownsScope() && request === requestSequence;
      try {
        const session = await sessionApi.get(sessionId, { signal: controller.signal });
        if (!ownsRequest()) return;
        if (session?.id !== sessionId) {
          publish(null);
          return;
        }
        const title = session.name?.trim() || null;
        publish(title);
        if (mayPoll && !title) retryTimer = setTimeout(() => { void load(false); }, 3000);
      } catch {
        if (!ownsRequest()) return;
        publish(null);
      }
    };
    const stopListening = listenSessionTitleUpdated((detail) => {
      if (!ownsScope() || detail.sessionId !== sessionId) return;
      // Events carry no authenticated subject. Even a matching session ID does
      // not authorize their title; use them only to invalidate this read.
      clearTimeout(retryTimer);
      void load(false);
    });
    void load(true);
    return () => {
      active = false;
      controller.abort();
      clearTimeout(retryTimer);
      stopListening();
    };
  }, [scope, sessionId, generation]);

  return scope && snapshot?.generation === generation ? snapshot.title : null;
}
