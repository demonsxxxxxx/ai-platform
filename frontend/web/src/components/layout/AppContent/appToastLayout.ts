export const APP_TOASTER_CLASS_NAME = "app-toaster";
export const APP_TOAST_SIDEBAR_OFFSET_VAR = "--app-toast-sidebar-offset";

export function getAppToastSidebarOffset({
  sidebarCollapsed,
}: {
  sidebarCollapsed: boolean;
}): string {
  return sidebarCollapsed
    ? "var(--sidebar-rail-width)"
    : "var(--sidebar-width)";
}
