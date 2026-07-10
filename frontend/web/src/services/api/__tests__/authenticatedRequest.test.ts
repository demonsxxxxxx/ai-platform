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
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
    },
  });
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      dispatchEvent() {
        return true;
      },
      location: {
        pathname: "/chat",
        search: "",
      },
    },
  });

  return {
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
