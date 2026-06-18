import test from "node:test";
import assert from "node:assert/strict";

import { notificationApi } from "../notification.ts";

function installFetchStub(body: unknown) {
  const originalFetch = Object.getOwnPropertyDescriptor(globalThis, "fetch");
  const originalLocalStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    "localStorage",
  );

  Object.defineProperty(globalThis, "fetch", {
    configurable: true,
    value: async () =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
  });
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
    },
  });

  return () => {
    if (originalFetch) {
      Object.defineProperty(globalThis, "fetch", originalFetch);
    } else {
      delete (globalThis as { fetch?: typeof fetch }).fetch;
    }
    if (originalLocalStorage) {
      Object.defineProperty(globalThis, "localStorage", originalLocalStorage);
    } else {
      delete (globalThis as { localStorage?: Storage }).localStorage;
    }
  };
}

test("active notifications accepts ai-platform envelope response", async () => {
  const restore = installFetchStub({
    notifications: [
      {
        id: "n1",
        title: "Smoke notification",
        content: "Active notification projection",
        level: "info",
        type: "system",
      },
    ],
  });

  try {
    const notifications = await notificationApi.getActive();

    assert.equal(notifications.length, 1);
    assert.equal(notifications[0].id, "n1");
    assert.equal(notifications[0].title_i18n.en, "Smoke notification");
    assert.equal(
      notifications[0].content_i18n.en,
      "Active notification projection",
    );
  } finally {
    restore();
  }
});

test("active notifications still accepts legacy array response", async () => {
  const restore = installFetchStub([
    {
      id: "n1",
      title_i18n: {
        en: "English",
        zh: "中文",
        ja: "",
        ko: "",
        ru: "",
      },
      content_i18n: {
        en: "Content",
        zh: "内容",
        ja: "",
        ko: "",
        ru: "",
      },
      type: "info",
      start_time: null,
      end_time: null,
      is_active: true,
      created_at: "",
      updated_at: "",
      created_by: "",
    },
  ]);

  try {
    const notifications = await notificationApi.getActive();

    assert.equal(notifications.length, 1);
    assert.equal(notifications[0].title_i18n.en, "English");
  } finally {
    restore();
  }
});
