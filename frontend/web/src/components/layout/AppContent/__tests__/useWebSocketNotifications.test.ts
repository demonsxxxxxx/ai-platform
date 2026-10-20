import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { Script } from "node:vm";
import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToString } from "react-dom/server";
import ts from "typescript";
import * as guards from "../taskNotificationGuards";
import { buildTaskNotificationCopy } from "../taskNotificationContent";
import { getUnreadCount, mergeUnreadUpdate } from "../../../sidebar/unreadCounts";

type UnreadCallback = (sessionId: string, unreadCount: number) => void;
type Completion = {
  data: { session_id: string; run_id: string; status: string; unread_count: number };
};
type NotificationOptions = { onTaskComplete: (notification: Completion) => Promise<void> };

function loadNotificationOwner(visibilityState: DocumentVisibilityState = "visible") {
  let options: NotificationOptions | undefined;
  const reads: string[] = [];
  const toasts: unknown[] = [];
  const modules: Record<string, unknown> = {
    react: React,
    "react/jsx-runtime": jsxRuntime,
    "react-router-dom": { useNavigate: () => () => {} },
    "@lobehub/ui/base-ui": { toast: { info: (content: unknown) => toasts.push(content) } },
    "lucide-react": { Bell: () => null, X: () => null },
    "../../../hooks/useWebSocket": {
      useWebSocket: (value: NotificationOptions) => { options = value; },
    },
    "../../../hooks/useBrowserNotification": {
      useBrowserNotification: () => ({ isSupported: false, permission: "default" }),
    },
    "../../../services/api": {
      sessionApi: {
        get: async (id: string) => { reads.push(`session:${id}`); return { name: id }; },
        getEvents: async (id: string) => { reads.push(`events:${id}`); return { events: [] }; },
      },
    },
    "./taskNotificationGuards": guards,
    "./taskNotificationContent": { buildTaskNotificationCopy },
    "../../../utils/mobile": { isMobileDevice: () => false },
    "../../../i18n": { default: { getFixedT: () => (key: string) => key } },
  };
  // Execute the production hook and callback with real React and unread/notification
  // owners. Only external transport, notification and navigation ports are controlled.
  // useWebSocket currently has no transport; no production test hook is introduced.
  const source = readFileSync(new URL("../useWebSocketNotifications.tsx", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  const owner = {} as {
    useWebSocketNotifications: (value: { sessionId: string; onSessionUnread: UnreadCallback }) => void;
  };
  new Script(compiled, { filename: "useWebSocketNotifications.tsx" }).runInNewContext({
    exports: owner,
    document: { visibilityState },
    console,
    require(name: string) {
      assert.ok(name in modules, `Unexpected notification dependency: ${name}`);
      return modules[name];
    },
  });
  return {
    reads,
    toasts,
    render(sessionId: string, onSessionUnread: UnreadCallback) {
      renderToString(React.createElement(function NotificationOwner() {
        owner.useWebSocketNotifications({ sessionId, onSessionUnread });
        return null;
      }));
      assert.ok(options);
      return options.onTaskComplete;
    },
  };
}

function completion(sessionId: string, unreadCount: number, status = "completed"): Completion {
  return { data: { session_id: sessionId, run_id: `run-${sessionId}`, status, unread_count: unreadCount } };
}

test("visible active completion clears only local unread without a read-mark transport", async () => {
  const owner = loadNotificationOwner();
  let unread = new Map([["active", { count: 2 }], ["background", { count: 3 }]]);
  const updates: Array<[string, number]> = [];
  const onComplete = owner.render("active", (sessionId, unreadCount) => {
    updates.push([sessionId, unreadCount]);
    unread = mergeUnreadUpdate(unread, { sessionId, unreadCount });
  });

  await onComplete(completion("active", 4));
  await onComplete(completion("active", 4, "failed"));

  assert.deepEqual(updates, [["active", 0], ["active", 0]]);
  assert.equal(unread.has("active"), false);
  assert.equal(unread.get("background")?.count, 3);
  assert.equal(getUnreadCount({ loadedSessions: [], unreadBySession: unread }), 3);
  assert.deepEqual(owner.reads, []);
  assert.deepEqual(owner.toasts, []);
});

test("after navigation the notification owner preserves background unread and clears the new active session", async () => {
  const owner = loadNotificationOwner();
  let unread = new Map([["a", { count: 1 }], ["b", { count: 2 }], ["other", { count: 3 }]]);
  const update: UnreadCallback = (sessionId, unreadCount) => {
    unread = mergeUnreadUpdate(unread, { sessionId, unreadCount });
  };
  owner.render("a", update);
  const onComplete = owner.render("b", update);

  await onComplete(completion("a", 5));
  await onComplete(completion("b", 4));

  assert.equal(unread.get("a")?.count, 5);
  assert.equal(unread.has("b"), false);
  assert.equal(unread.get("other")?.count, 3);
  assert.equal(getUnreadCount({ loadedSessions: [], unreadBySession: unread }), 8);
  assert.deepEqual(owner.reads, ["session:a", "events:a"]);
  assert.equal(owner.toasts.length, 1);
});

test("a hidden active session still surfaces completion without clearing local unread", async () => {
  const owner = loadNotificationOwner("hidden");
  const updates: Array<[string, number]> = [];
  const onComplete = owner.render("active", (id, count) => updates.push([id, count]));

  await onComplete(completion("active", 4));

  assert.deepEqual(updates, []);
  assert.deepEqual(owner.reads, ["session:active", "events:active"]);
  assert.equal(owner.toasts.length, 1);
});
