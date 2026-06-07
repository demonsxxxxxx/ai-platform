import assert from "node:assert/strict";
import test from "node:test";

import { selectNotificationDeliveryMode } from "../browserNotificationDelivery.ts";

test("uses service worker notifications on mobile when available", () => {
  assert.equal(
    selectNotificationDeliveryMode({
      isMobile: true,
      permission: "granted",
      hasNotificationApi: true,
      hasServiceWorkerNotification: true,
    }),
    "service-worker",
  );
});

test("does not fall back to the Notification constructor on mobile", () => {
  assert.equal(
    selectNotificationDeliveryMode({
      isMobile: true,
      permission: "granted",
      hasNotificationApi: true,
      hasServiceWorkerNotification: false,
    }),
    "none",
  );
});

test("keeps the constructor fallback for desktop browsers", () => {
  assert.equal(
    selectNotificationDeliveryMode({
      isMobile: false,
      permission: "granted",
      hasNotificationApi: true,
      hasServiceWorkerNotification: false,
    }),
    "constructor",
  );
});

test("does not deliver notifications before permission is granted", () => {
  assert.equal(
    selectNotificationDeliveryMode({
      isMobile: false,
      permission: "default",
      hasNotificationApi: true,
      hasServiceWorkerNotification: true,
    }),
    "none",
  );
});
