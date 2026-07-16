import { useRef } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "react-hot-toast";
import { Bell, X } from "lucide-react";
import { useWebSocket } from "../../../hooks/useWebSocket";
import { useBrowserNotification } from "../../../hooks/useBrowserNotification";
import { sessionApi } from "../../../services/api";
import {
  shouldAttemptBrowserNotification,
  shouldSurfaceTaskNotification,
} from "./taskNotificationGuards";
import { buildTaskNotificationCopy } from "./taskNotificationContent";
import { isMobileDevice } from "../../../utils/mobile";
import i18n from "../../../i18n";

interface UseWebSocketNotificationsOptions {
  sessionId: string | null;
  enabled?: boolean;
  onSessionUnread?: (
    sessionId: string,
    unreadCount: number,
    projectId?: string | null,
    isFavorite?: boolean,
  ) => void;
}

export function useWebSocketNotifications({
  sessionId,
  enabled = true,
  onSessionUnread,
}: UseWebSocketNotificationsOptions) {
  const t = i18n.getFixedT("zh");
  const navigate = useNavigate();
  const { notify, isSupported, permission } = useBrowserNotification();
  const onSessionUnreadRef = useRef(onSessionUnread);
  onSessionUnreadRef.current = onSessionUnread;

  // WebSocket for task completion notifications
  useWebSocket({
    enabled,
    onTaskComplete: async (notification: {
      data: {
        session_id: string;
        run_id: string;
        status: string;
        message?: string;
        unread_count?: number;
        project_id?: string | null;
        is_favorite?: boolean;
      };
    }) => {
      const {
        session_id,
        run_id,
        status,
        message,
        unread_count,
        project_id,
        is_favorite,
      } = notification.data;

      // 通知侧边栏更新 unread_count（仅非当前 session）
      if (session_id !== sessionId && unread_count !== undefined) {
        onSessionUnreadRef.current?.(
          session_id,
          unread_count,
          project_id,
          is_favorite,
        );
      }

      const visibilityState =
        typeof document === "undefined" ? "visible" : document.visibilityState;
      const shouldSurface = shouldSurfaceTaskNotification({
        notificationSessionId: session_id,
        currentSessionId: sessionId,
        visibilityState,
      });

      if (!shouldSurface) {
        sessionApi.markRead(session_id).catch(() => {});
        onSessionUnreadRef.current?.(session_id, 0, project_id, is_favorite);
        return;
      }

      const [sessionResult, eventsResult] = await Promise.all([
        sessionApi.get(session_id).catch((err) => {
          console.warn(
            "[AppContent] Failed to fetch session name for notification:",
            err,
          );
          return null;
        }),
        status === "completed"
          ? sessionApi.getEvents(session_id, { run_id }).catch((err) => {
              console.warn(
                "[AppContent] Failed to fetch session events for notification:",
                err,
              );
              return null;
            })
          : Promise.resolve(null),
      ]);

      const notificationCopy = buildTaskNotificationCopy({
        sessionName: sessionResult?.name,
        status: status === "completed" ? "completed" : "failed",
        fallbackMessage: message,
        events: eventsResult?.events,
        successLabel: t("notification.taskCompleted"),
        failureLabel: t("notification.taskFailed"),
      });

      const navigateToSession = () => {
        navigate(`/chat/${session_id}`, {
          replace: true,
          state: { externalNavigate: true, scrollToBottom: true },
        });
      };

      // Show browser notification (if permitted)
      if (
        shouldAttemptBrowserNotification({
          isSupported,
          cachedPermission: permission,
        })
      ) {
        notify(notificationCopy.title, {
          body: notificationCopy.body,
          onClick: navigateToSession,
          url: `/chat/${session_id}`,
        });
      }

      toast.custom(
        (visible) => (
          <div
            className={`group relative pointer-events-auto cursor-pointer select-none max-w-[min(92vw,24rem)] w-full rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-4 py-3.5 text-[var(--theme-text)] shadow-[0_16px_40px_-28px_rgba(15,23,42,0.45)] transition-all ${
              visible
                ? "translate-y-0 opacity-100"
                : "translate-y-1.5 opacity-0"
            }`}
            onClick={(e) => {
              e.stopPropagation();
              navigateToSession();
              toast.remove();
            }}
          >
            <button
              onClick={(e) => {
                e.stopPropagation();
                toast.remove();
              }}
              className="absolute top-1.5 right-1.5 z-10 rounded-full p-0.5 text-stone-400 opacity-0 transition-opacity hover:text-stone-600 group-hover:opacity-100 dark:text-stone-500 dark:hover:text-stone-300"
              aria-label={t("common.close")}
            >
              <X size={12} />
            </button>

            <div className="flex items-center gap-3 text-left">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]">
                <Bell size={16} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="line-clamp-1 text-[13px] font-semibold leading-tight">
                  {notificationCopy.title}
                </div>
                <div className="mt-0.5 line-clamp-1 text-xs leading-snug text-stone-500 dark:text-stone-400">
                  {notificationCopy.body}
                </div>
              </div>
            </div>
          </div>
        ),
        {
          duration: isMobileDevice() ? 5_000 : 10_000,
          position: "top-right",
          style: {
            background: "transparent",
            padding: 0,
            boxShadow: "none",
            border: "none",
            borderRadius: 0,
            overflow: "visible",
          },
        },
      );
    },
  });
}
