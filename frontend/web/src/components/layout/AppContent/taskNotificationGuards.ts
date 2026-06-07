export interface TaskNotificationSurfaceInput {
  notificationSessionId: string;
  currentSessionId: string | null;
  visibilityState: DocumentVisibilityState;
}

export interface BrowserNotificationAttemptInput {
  isSupported: boolean;
  cachedPermission: NotificationPermission;
}

export function shouldSurfaceTaskNotification({
  notificationSessionId,
  currentSessionId,
  visibilityState,
}: TaskNotificationSurfaceInput): boolean {
  return !(
    currentSessionId === notificationSessionId && visibilityState === "visible"
  );
}

export function shouldAttemptBrowserNotification({
  isSupported,
}: BrowserNotificationAttemptInput): boolean {
  return isSupported;
}
