/// <reference lib="webworker" />

import { CacheableResponsePlugin } from "workbox-cacheable-response";
import { clientsClaim } from "workbox-core";
import { ExpirationPlugin } from "workbox-expiration";
import { cleanupOutdatedCaches, precacheAndRoute } from "workbox-precaching";
import { registerRoute } from "workbox-routing";
import { NetworkFirst, StaleWhileRevalidate } from "workbox-strategies";
import { isPwaSkipWaitingMessage } from "./pwaGuards";
import { getPwaRequestKind } from "./pwaRouting";

declare const self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: Array<unknown>;
};

const PRECACHE_MANIFEST = self.__WB_MANIFEST;
const BUILD_CACHE_VERSION = getBuildCacheVersion(PRECACHE_MANIFEST);
const APP_SHELL_CACHE = `ai-platform-app-shell-${BUILD_CACHE_VERSION}`;
const STATIC_CACHE = `ai-platform-static-${BUILD_CACHE_VERSION}`;
const RUNTIME_CACHE_PREFIXES = [
  "ai-platform-app-shell-",
  "ai-platform-static-",
] as const;
const OFFLINE_URL = "/offline.html";

cleanupOutdatedCaches();
precacheAndRoute(PRECACHE_MANIFEST);
clientsClaim();

function getBuildCacheVersion(manifest: Array<unknown>): string {
  const signature = manifest
    .map((entry) => {
      if (typeof entry === "string") return entry;
      if (!entry || typeof entry !== "object") return "";
      const item = entry as { url?: unknown; revision?: unknown };
      return `${String(item.url ?? "")}:${String(item.revision ?? "")}`;
    })
    .sort()
    .join("|");

  let hash = 0;
  for (let index = 0; index < signature.length; index += 1) {
    hash = (hash * 31 + signature.charCodeAt(index)) >>> 0;
  }

  return hash.toString(36) || "empty";
}

function isAiPlatformRuntimeCache(cacheName: string): boolean {
  return RUNTIME_CACHE_PREFIXES.some((prefix) => cacheName.startsWith(prefix));
}

async function deleteOutdatedRuntimeCaches(): Promise<void> {
  const expectedCaches = new Set([APP_SHELL_CACHE, STATIC_CACHE]);
  const cacheNames = await caches.keys();

  await Promise.all(
    cacheNames
      .filter(
        (cacheName) =>
          isAiPlatformRuntimeCache(cacheName) && !expectedCaches.has(cacheName),
      )
      .map((cacheName) => caches.delete(cacheName)),
  );
}

self.addEventListener("activate", (event) => {
  event.waitUntil(deleteOutdatedRuntimeCaches());
});

self.addEventListener("message", (event) => {
  if (!isPwaSkipWaitingMessage(event.data)) return;

  event.waitUntil(self.skipWaiting());
});

const navigationStrategy = new NetworkFirst({
  cacheName: APP_SHELL_CACHE,
  networkTimeoutSeconds: 4,
  plugins: [
    new CacheableResponsePlugin({
      statuses: [200],
    }),
  ],
});

async function getOfflineFallback(): Promise<Response> {
  const cachedFallback =
    (await caches.match(OFFLINE_URL)) || (await caches.match("/index.html"));

  return (
    cachedFallback ||
    new Response("AI Platform is offline.", {
      status: 503,
      statusText: "Service Unavailable",
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    })
  );
}

registerRoute(
  ({ request }) =>
    getPwaRequestKind({
      method: request.method,
      mode: request.mode,
      url: request.url,
      scopeOrigin: self.location.origin,
      accept: request.headers.get("accept"),
    }) === "navigation",
  async (options) => {
    try {
      return (await navigationStrategy.handle(options)) || getOfflineFallback();
    } catch {
      return getOfflineFallback();
    }
  },
);

registerRoute(
  ({ request }) =>
    getPwaRequestKind({
      method: request.method,
      mode: request.mode,
      url: request.url,
      scopeOrigin: self.location.origin,
      accept: request.headers.get("accept"),
    }) === "static-asset",
  new StaleWhileRevalidate({
    cacheName: STATIC_CACHE,
    plugins: [
      new CacheableResponsePlugin({
        statuses: [0, 200],
      }),
      new ExpirationPlugin({
        maxEntries: 220,
        maxAgeSeconds: 60 * 60 * 24 * 30,
      }),
    ],
  }),
);

self.addEventListener("push", (event) => {
  if (!self.registration?.showNotification) return;

  let payload: {
    title?: string;
    body?: string;
    message?: string;
    icon?: string;
    badge?: string;
    url?: string;
  } = {};

  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { body: event.data?.text() };
  }

  const title = payload.title || "AI Platform";
  const options: NotificationOptions = {
    body:
      payload.body ||
      payload.message ||
      "You have a new AI Platform update.",
    icon: payload.icon || "/icons/icon-192.png",
    badge: payload.badge || "/icons/icon-192.png",
    data: {
      url: payload.url || "/chat",
    },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const targetUrl = new URL(
    event.notification.data?.url || "/chat",
    self.location.origin,
  );

  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clients) => {
        const existingClient = clients.find(
          (client): client is WindowClient =>
            "focus" in client &&
            "navigate" in client &&
            new URL(client.url).origin === targetUrl.origin,
        );

        if (existingClient) {
          existingClient.focus();
          return existingClient.navigate(targetUrl.href);
        }

        return self.clients.openWindow(targetUrl.href);
      }),
  );
});
