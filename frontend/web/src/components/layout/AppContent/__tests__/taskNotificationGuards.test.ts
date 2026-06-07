import assert from "node:assert/strict";
import test from "node:test";

import {
  shouldAttemptBrowserNotification,
  shouldSurfaceTaskNotification,
} from "../taskNotificationGuards.ts";

test("does not surface task notifications for the visible active session", () => {
  assert.equal(
    shouldSurfaceTaskNotification({
      notificationSessionId: "session-1",
      currentSessionId: "session-1",
      visibilityState: "visible",
    }),
    false,
  );
});

test("surfaces task notifications for inactive or hidden sessions", () => {
  assert.equal(
    shouldSurfaceTaskNotification({
      notificationSessionId: "session-2",
      currentSessionId: "session-1",
      visibilityState: "visible",
    }),
    true,
  );
  assert.equal(
    shouldSurfaceTaskNotification({
      notificationSessionId: "session-1",
      currentSessionId: "session-1",
      visibilityState: "hidden",
    }),
    true,
  );
});

test("attempts browser notifications whenever the API is supported", () => {
  assert.equal(
    shouldAttemptBrowserNotification({
      isSupported: true,
      cachedPermission: "default",
    }),
    true,
  );
  assert.equal(
    shouldAttemptBrowserNotification({
      isSupported: true,
      cachedPermission: "denied",
    }),
    true,
  );
  assert.equal(
    shouldAttemptBrowserNotification({
      isSupported: false,
      cachedPermission: "granted",
    }),
    false,
  );
});
