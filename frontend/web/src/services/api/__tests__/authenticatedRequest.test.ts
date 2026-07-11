import test from "node:test";
import assert from "node:assert/strict";

import { authenticatedRequest } from "../authenticatedRequest.ts";

function installAuthenticatedRequestStubs(
  fetchImpl: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>,
) {
  const originalFetch = Object.getOwnPropertyDescriptor(globalThis, "fetch");
  const originalLocalStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    "localStorage",
  );
  const originalSessionStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    "sessionStorage",
  );
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const sessionStore = new Map<string, string>();
  const events: string[] = [];

  Object.defineProperty(globalThis, "fetch", {
    configurable: true,
    value: fetchImpl,
  });
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) =>
        key === "ai_platform_session_present" ? "session-marker" : null,
      setItem: () => {},
      removeItem: () => {},
    },
  });
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => sessionStore.get(key) ?? null,
      setItem: (key: string, value: string) => {
        sessionStore.set(key, value);
      },
      removeItem: (key: string) => {
        sessionStore.delete(key);
      },
    },
  });
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      dispatchEvent(event: Event) {
        events.push(event.type);
        return true;
      },
      location: {
        pathname: "/chat",
        search: "",
      },
    },
  });

  return {
    events,
    sessionStore,
    restore() {
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
      if (originalSessionStorage) {
        Object.defineProperty(globalThis, "sessionStorage", originalSessionStorage);
      } else {
        delete (globalThis as { sessionStorage?: Storage }).sessionStorage;
      }
      if (originalWindow) {
        Object.defineProperty(globalThis, "window", originalWindow);
      } else {
        delete (globalThis as { window?: Window }).window;
      }
    },
  };
}

test("authenticatedRequest strips caller Authorization headers in browser mode", async () => {
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  const stubs = installAuthenticatedRequestStubs(async (input, init) => {
    calls.push({ input: String(input), init });
    return new Response("ok", { status: 200 });
  });

  try {
    const response = await authenticatedRequest("/api/sessions", {
      headers: {
        Authorization: "Bearer leaked-token",
        "X-Test": "1",
      },
    });

    assert.equal(response.status, 200);
    const headers = new Headers(calls[0].init?.headers);
    assert.equal(headers.has("Authorization"), false);
    assert.equal(headers.get("X-Test"), "1");
    assert.equal(calls[0].init?.credentials, "include");
  } finally {
    stubs.restore();
  }
});

test("authenticatedRequest preserves redirect path when cookie session probe is revoked", async () => {
  const calls: string[] = [];
  const stubs = installAuthenticatedRequestStubs(async (input) => {
    const url = String(input);
    calls.push(url);
    return new Response(JSON.stringify({ detail: "unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  });

  try {
    await assert.rejects(
      () => authenticatedRequest("/api/sessions"),
      /Unauthorized/,
    );
    assert.deepEqual(calls, ["/api/sessions", "/api/ai/auth/me"]);
    assert.deepEqual(stubs.events, ["auth:logout"]);
    assert.equal(stubs.sessionStore.get("redirect_after_login"), "/chat");
  } finally {
    stubs.restore();
  }
});
