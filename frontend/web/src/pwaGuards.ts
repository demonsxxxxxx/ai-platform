export interface PwaRegistrationSupport {
  isProduction: boolean;
  hasServiceWorker: boolean;
}

export const PWA_UPDATE_AVAILABLE_EVENT = "lambchat:pwa-update-available";
export const PWA_SKIP_WAITING_MESSAGE = "SKIP_WAITING";

export function shouldRegisterPwa({
  isProduction,
  hasServiceWorker,
}: PwaRegistrationSupport): boolean {
  return isProduction && hasServiceWorker;
}

export interface PwaUpdateState {
  hasController: boolean;
  workerState: ServiceWorkerState | string | null | undefined;
}

export function isPwaUpdateReady({
  hasController,
  workerState,
}: PwaUpdateState): boolean {
  return hasController && workerState === "installed";
}

export function isPwaSkipWaitingMessage(data: unknown): boolean {
  if (data === PWA_SKIP_WAITING_MESSAGE) return true;
  if (!data || typeof data !== "object") return false;

  return (
    "type" in data &&
    (data as { type?: unknown }).type === PWA_SKIP_WAITING_MESSAGE
  );
}
