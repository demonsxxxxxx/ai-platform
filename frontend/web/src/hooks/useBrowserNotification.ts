import { useCallback, useEffect, useState } from "react";
import {
  hasServiceWorkerNotificationSupport,
  selectNotificationDeliveryMode,
} from "./browserNotificationDelivery";
import { isMobileDevice, resetMobileViewport } from "../utils/mobile";

interface NotificationOptions {
  body?: string;
  icon?: string;
  badge?: string;
  tag?: string;
  data?: unknown;
  onClick?: () => void;
  url?: string; // URL to navigate when notification is clicked
}

export function useBrowserNotification() {
  const [permission, setPermission] =
    useState<NotificationPermission>("default");
  const [isSupported, setIsSupported] = useState(false);
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const supported = "Notification" in window;
      setIsSupported(supported);
      setIsMobile(isMobileDevice());

      if (supported) {
        setPermission(Notification.permission);
      }
    }
  }, []);

  const requestPermission = useCallback(async (): Promise<boolean> => {
    if (!("Notification" in window)) {
      console.warn("[BrowserNotification] Not supported");
      return false;
    }

    if (Notification.permission === "granted") {
      return true;
    }

    if (Notification.permission === "denied") {
      console.warn("[BrowserNotification] Permission denied");
      return false;
    }

    try {
      const result = await Notification.requestPermission();
      setPermission(result);

      // Fix mobile viewport zoom after permission dialog dismissal
      // Mobile browsers (especially iOS Safari) may zoom in when showing system dialogs
      resetMobileViewport();

      return result === "granted";
    } catch (e) {
      console.error("[BrowserNotification] Request permission failed:", e);
      return false;
    }
  }, []);

  const notify = useCallback(
    async (
      title: string,
      options?: NotificationOptions,
    ): Promise<Notification | null> => {
      if (!("Notification" in window)) {
        console.warn("[BrowserNotification] Not supported");
        return null;
      }

      if (Notification.permission !== "granted") {
        console.warn("[BrowserNotification] Permission not granted");
        return null;
      }

      const deliveryMode = selectNotificationDeliveryMode({
        isMobile,
        permission: Notification.permission,
        hasNotificationApi: true,
        hasServiceWorkerNotification: hasServiceWorkerNotificationSupport(),
      });

      if (deliveryMode === "service-worker") {
        try {
          const registration = await navigator.serviceWorker.ready;
          await registration.showNotification(title, {
            icon: options?.icon || "/icons/icon-192.png",
            badge: options?.badge || "/icons/icon-192.png",
            tag: options?.tag || "ai-platform-notification",
            body: options?.body,
            data: {
              ...(typeof options?.data === "object" && options.data !== null
                ? options.data
                : {}),
              url: options?.url || "/chat",
            },
          });
          return null;
        } catch (e) {
          console.error("[BrowserNotification] Service worker show failed:", e);
          if (isMobile) {
            return null;
          }
        }
      }

      if (deliveryMode === "none") {
        console.warn("[BrowserNotification] No supported delivery method");
        return null;
      }

      try {
        const notification = new Notification(title, {
          icon: "/icons/icon.svg",
          badge: "/icons/icon.svg",
          tag: "ai-platform-notification",
          ...options,
        });

        if (options?.onClick) {
          notification.onclick = () => {
            options.onClick!();
            notification.close();
            window.focus();
          };
        }

        // Auto close after 5 seconds
        setTimeout(() => notification.close(), 5000);

        return notification;
      } catch (e) {
        console.error("[BrowserNotification] Show failed:", e);
        return null;
      }
    },
    [isMobile],
  );

  return {
    isSupported,
    permission,
    requestPermission,
    notify,
    isMobile,
  };
}
