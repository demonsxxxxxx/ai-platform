import type { BackendSession } from "../../services/api/session";

export interface UnreadEntry {
  count: number;
}

export type UnreadBySession = Map<string, UnreadEntry>;

export function mergeUnreadUpdate(
  unreadBySession: UnreadBySession,
  update: {
    sessionId: string;
    unreadCount: number;
  },
): UnreadBySession {
  const next = new Map(unreadBySession);
  if (update.unreadCount <= 0) {
    next.delete(update.sessionId);
    return next;
  }

  next.set(update.sessionId, {
    count: update.unreadCount,
  });
  return next;
}

/** Combines loaded session unread counts with websocket updates not yet loaded. */
export function getUnreadCount({
  loadedSessions,
  unreadBySession,
}: {
  loadedSessions: BackendSession[];
  unreadBySession: UnreadBySession;
}): number {
  const loadedIds = new Set(loadedSessions.map((session) => session.id));
  const loadedCount = loadedSessions.reduce(
    (total, session) => total + Math.max(0, session.unread_count ?? 0),
    0,
  );
  const externalCount = Array.from(unreadBySession.entries()).reduce(
    (total, [sessionId, entry]) =>
      !loadedIds.has(sessionId)
        ? total + entry.count
        : total,
    0,
  );
  return loadedCount + externalCount;
}

export function formatUnreadCount(count: number): string {
  return count > 99 ? "99+" : String(count);
}
