import {
  PWA_SKIP_WAITING_MESSAGE,
  PWA_UPDATE_AVAILABLE_EVENT,
  isPwaUpdateReady,
  shouldRegisterPwa,
} from "./pwaGuards";

export interface AiPlatformPwaUpdateEventDetail {
  registration: ServiceWorkerRegistration;
}

let reloadWhenControllerChanges = false;

function notifyPwaUpdateAvailable(registration: ServiceWorkerRegistration) {
  window.dispatchEvent(
    new CustomEvent<AiPlatformPwaUpdateEventDetail>(
      PWA_UPDATE_AVAILABLE_EVENT,
      {
        detail: { registration },
      },
    ),
  );
  activateWaitingAiPlatformPwaUpdate(registration);
}

function watchForPwaUpdates(registration: ServiceWorkerRegistration) {
  if (registration.waiting && navigator.serviceWorker.controller) {
    notifyPwaUpdateAvailable(registration);
  }

  registration.addEventListener("updatefound", () => {
    const worker = registration.installing;
    if (!worker) return;

    worker.addEventListener("statechange", () => {
      if (
        isPwaUpdateReady({
          hasController: Boolean(navigator.serviceWorker.controller),
          workerState: worker.state,
        })
      ) {
        notifyPwaUpdateAvailable(registration);
      }
    });
  });
}

export function activateWaitingAiPlatformPwaUpdate(
  registration: ServiceWorkerRegistration,
): boolean {
  if (!registration.waiting) return false;

  reloadWhenControllerChanges = true;
  registration.waiting.postMessage({ type: PWA_SKIP_WAITING_MESSAGE });
  return true;
}

export function registerAiPlatformPwa(): void {
  const hasServiceWorker =
    typeof navigator !== "undefined" && "serviceWorker" in navigator;

  if (
    !shouldRegisterPwa({
      isProduction: import.meta.env.PROD,
      hasServiceWorker,
    })
  ) {
    return;
  }

  window.addEventListener("load", () => {
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (!reloadWhenControllerChanges) return;
      reloadWhenControllerChanges = false;
      window.location.reload();
    });

    navigator.serviceWorker
      .register("/sw.js", { scope: "/", updateViaCache: "none" })
      .then((registration) => {
        watchForPwaUpdates(registration);
        return registration.update().catch(() => undefined);
      })
      .catch((error) => {
        console.warn("[PWA] Service worker registration failed:", error);
      });
  });
}
