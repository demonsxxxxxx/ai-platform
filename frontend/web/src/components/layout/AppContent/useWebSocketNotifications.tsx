import { useRef } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "@lobehub/ui/base-ui";
import { Bell } from "lucide-react";
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
      };
    }) => {
      const {
        session_id,
        run_id,
        status,
        message,
        unread_count,
      } = notification.data;

      // 通知侧边栏更新 unread_count（仅非当前 session）
      if (session_id !== sessionId && unread_count !== undefined) {
        onSessionUnreadRef.current?.(
          session_id,
          unread_count,
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
        onSessionUnreadRef.current?.(session_id, 0);
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

      toast.info({
        title: notificationCopy.title,
        description: notificationCopy.body,
        icon: Bell,
        duration: isMobileDevice() ? 5_000 : 10_000,
        placement: "top",
        hideCloseButton: true,
        actions: [
          { label: t("common.open", "打开"), onClick: navigateToSession },
          { label: t("common.close", "关闭"), variant: "secondary" },
        ],
      });
    },
  });
}
