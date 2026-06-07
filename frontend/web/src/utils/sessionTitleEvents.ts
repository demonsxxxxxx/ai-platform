export const SESSION_TITLE_UPDATED_EVENT = "lambchat:session-title-updated";

export interface SessionTitleUpdatedDetail {
  sessionId: string;
  title: string;
}

const cachedSessionTitles = new Map<string, string>();

function getEventTarget(target?: EventTarget): EventTarget | null {
  if (target) return target;
  if (typeof window === "undefined") return null;
  return window;
}

function normalizeDetail(
  detail: Partial<SessionTitleUpdatedDetail> | null | undefined,
): SessionTitleUpdatedDetail | null {
  const sessionId = detail?.sessionId?.trim();
  const title = detail?.title?.trim();
  if (!sessionId || !title) return null;
  return { sessionId, title };
}

export function getCachedSessionTitle(sessionId: string): string | null {
  return cachedSessionTitles.get(sessionId) ?? null;
}

export function dispatchSessionTitleUpdated(
  detail: SessionTitleUpdatedDetail,
  target?: EventTarget,
): void {
  const normalized = normalizeDetail(detail);
  const eventTarget = getEventTarget(target);
  if (!normalized || !eventTarget) return;

  cachedSessionTitles.set(normalized.sessionId, normalized.title);
  eventTarget.dispatchEvent(
    new CustomEvent<SessionTitleUpdatedDetail>(SESSION_TITLE_UPDATED_EVENT, {
      detail: normalized,
    }),
  );
}

export function listenSessionTitleUpdated(
  listener: (detail: SessionTitleUpdatedDetail) => void,
  target?: EventTarget,
): () => void {
  const eventTarget = getEventTarget(target);
  if (!eventTarget) return () => undefined;

  const handleTitleUpdate = (event: Event) => {
    const normalized = normalizeDetail(
      (event as CustomEvent<Partial<SessionTitleUpdatedDetail>>).detail,
    );
    if (normalized) {
      listener(normalized);
    }
  };

  eventTarget.addEventListener(SESSION_TITLE_UPDATED_EVENT, handleTitleUpdate);
  return () => {
    eventTarget.removeEventListener(
      SESSION_TITLE_UPDATED_EVENT,
      handleTitleUpdate,
    );
  };
}
