import type {
  ExternalStoreThreadData,
  ExternalStoreThreadListAdapter,
} from "@assistant-ui/react";
import type { BackendSession } from "../../../services/api";

export interface SessionThreadListSource {
  sessions: readonly BackendSession[];
  isLoading: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  loadMore: () => Promise<void>;
}

export interface SessionThreadListActions {
  onSelectSession: (sessionId: string) => Promise<void> | void;
  onNewSession?: () => Promise<void> | void;
  onRenameSession?: (
    sessionId: string,
    title: string,
  ) => Promise<void> | void;
  onDeleteSession?: (sessionId: string) => Promise<void> | void;
}

export interface SessionThreadListItem {
  id: string;
  title: string;
  time: string;
  unread: boolean;
  unreadCount: number;
}

export interface SessionThreadListAdapterView {
  adapter: ExternalStoreThreadListAdapter;
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMore: () => Promise<void>;
}

function sessionTitle(session: BackendSession, fallbackTitle: string): string {
  if (session.name?.trim()) return session.name;
  const metadataTitle = session.metadata?.title;
  if (typeof metadataTitle === "string" && metadataTitle.trim()) {
    return metadataTitle;
  }
  return fallbackTitle;
}

function sessionTime(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "";
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(timestamp);
}

export function toSessionThreadData(
  session: BackendSession,
  fallbackTitle: string,
): ExternalStoreThreadData<"regular"> {
  return {
    id: session.id,
    remoteId: session.id,
    title: sessionTitle(session, fallbackTitle),
    status: "regular",
  };
}

export function toSessionThreadListItem(
  session: BackendSession,
  fallbackTitle: string,
): SessionThreadListItem {
  const unreadCount = session.unread_count ?? 0;
  return {
    id: session.id,
    title: sessionTitle(session, fallbackTitle),
    time: sessionTime(session.updated_at),
    unread: unreadCount > 0,
    unreadCount,
  };
}

/**
 * Maps the existing session source to assistant-ui's controlled list contract.
 * The source remains the sole fetch, paging, mutation, and identity owner.
 */
export function createSessionThreadListAdapter(input: {
  source: SessionThreadListSource;
  currentSessionId: string | null;
  fallbackTitle: string;
  actions: SessionThreadListActions;
}): SessionThreadListAdapterView {
  const { source, currentSessionId, fallbackTitle, actions } = input;
  const threads = source.sessions.map((session) =>
    toSessionThreadData(session, fallbackTitle),
  );

  return {
    adapter: {
      threadId: currentSessionId ?? undefined,
      threads,
      isLoading: source.isLoading,
      onSwitchToNewThread: actions.onNewSession,
      onSwitchToThread: actions.onSelectSession,
      onRename: actions.onRenameSession,
      onDelete: actions.onDeleteSession,
    },
    hasMore: source.hasMore,
    isLoadingMore: source.isLoadingMore,
    loadMore: source.loadMore,
  };
}
