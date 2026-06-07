export type BrowserNotificationDeliveryMode =
  | "service-worker"
  | "constructor"
  | "none";

interface BrowserNotificationDeliveryInput {
  isMobile: boolean;
  permission: NotificationPermission;
  hasNotificationApi: boolean;
  hasServiceWorkerNotification: boolean;
}

export function selectNotificationDeliveryMode({
  isMobile,
  permission,
  hasNotificationApi,
  hasServiceWorkerNotification,
}: BrowserNotificationDeliveryInput): BrowserNotificationDeliveryMode {
  if (!hasNotificationApi || permission !== "granted") {
    return "none";
  }

  if (hasServiceWorkerNotification) {
    return "service-worker";
  }

  return isMobile ? "none" : "constructor";
}

export function hasServiceWorkerNotificationSupport(): boolean {
  return (
    typeof navigator !== "undefined" &&
    "serviceWorker" in navigator &&
    typeof ServiceWorkerRegistration !== "undefined" &&
    "showNotification" in ServiceWorkerRegistration.prototype
  );
}
