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
  const activeScope = useRef(scope);
  activeScope.current = scope;
  const [snapshot, setSnapshot] = useState<{ scope: string; title: string | null } | null>(null);

  useEffect(() => {
    if (!scope || !sessionId) return;
    let active = true;
    let authorized = false;
    let observedEvent = false;
    let eventTitle: string | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const ownsScope = () => active && activeScope.current === scope;
    const publish = (title: string | null) => {
      if (ownsScope()) setSnapshot({ scope, title });
    };
    // Never hydrate the process-wide session-only title cache: it has no
    // tenant/user ownership and cannot establish this subject's access.
    publish(null);
    const stopListening = listenSessionTitleUpdated((detail) => {
      if (!ownsScope() || detail.sessionId !== sessionId) return;
      observedEvent = true;
      eventTitle = detail.title;
      if (authorized) {
        clearTimeout(retryTimer);
        publish(eventTitle);
      }
    });

    const load = async (mayPoll: boolean) => {
      try {
        const session = await sessionApi.get(sessionId, { signal: controller.signal });
        if (!ownsScope()) return;
        authorized = session?.id === sessionId;
        if (!authorized) {
          eventTitle = null;
          observedEvent = false;
          publish(null);
          return;
        }
        // A title event observed after this scope began is newer than this
        // hydration. It may be shown only after access has been established.
        const title = observedEvent ? eventTitle : session?.name?.trim() || null;
        publish(title);
        if (mayPoll && !title) retryTimer = setTimeout(() => { void load(false); }, 3000);
      } catch {
        if (!ownsScope()) return;
        authorized = false;
        eventTitle = null;
        observedEvent = false;
        publish(null);
      }
    };
    void load(true);
    return () => {
      active = false;
      controller.abort();
      clearTimeout(retryTimer);
      stopListening();
    };
  }, [scope, sessionId]);

  return scope && snapshot?.scope === scope ? snapshot.title : null;
}
