import assert from "node:assert/strict";
import test from "node:test";

import {
  clearDocumentFetchCaches,
  fetchDocumentArrayBuffer,
  fetchDocumentText,
  fetchXlsxPreviewJson,
  shouldUseAuthenticatedDocumentRequest,
} from "../documentFetchCache.ts";
import { clearAuthState } from "../../../services/api/tokenManager.ts";

function installAuthBrowserStubs() {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const originalLocalStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    "localStorage",
  );
  const events: string[] = [];
  const removedKeys: string[] = [];

  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      location: {
        origin: "https://app.example.test",
      },
      dispatchEvent(event: Event) {
        events.push(event.type);
        return true;
      },
    },
  });
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      removeItem(key: string) {
        removedKeys.push(key);
      },
    },
  });

  return {
    events,
    removedKeys,
    restore() {
      if (originalWindow) {
        Object.defineProperty(globalThis, "window", originalWindow);
      } else {
        delete (globalThis as { window?: Window }).window;
      }
      if (originalLocalStorage) {
        Object.defineProperty(globalThis, "localStorage", originalLocalStorage);
      } else {
        delete (globalThis as { localStorage?: Storage }).localStorage;
      }
    },
  };
}

test("fetchDocumentArrayBuffer uses authenticated request for protected platform artifact downloads", async () => {
  clearDocumentFetchCaches();
  const authCalls: Array<string | URL | Request> = [];
  const fetchCalls: Array<string | URL | Request> = [];

  const buffer = await fetchDocumentArrayBuffer(
    "/api/ai/artifacts/artifact-1/download",
    {
      authenticatedRequest: async (input) => {
        authCalls.push(input);
        return new Response("protected-docx");
      },
      fetchImpl: async (input) => {
        fetchCalls.push(input);
        return new Response("unexpected-native-fetch");
      },
    },
  );

  assert.deepEqual(authCalls, ["/api/ai/artifacts/artifact-1/download"]);
  assert.deepEqual(fetchCalls, []);
  assert.equal(new TextDecoder().decode(buffer), "protected-docx");
});

test("fetchDocumentText rejects external signed URLs without native fetch", async () => {
  clearDocumentFetchCaches();
  const authCalls: Array<string | URL | Request> = [];
  const fetchCalls: Array<string | URL | Request> = [];

  await assert.rejects(
    () =>
      fetchDocumentText("https://example.com/file.docx", {
        authenticatedRequest: async (input) => {
          authCalls.push(input);
          return new Response("unexpected-authenticated-fetch");
        },
        fetchImpl: async (input) => {
          fetchCalls.push(input);
          return new Response("external-docx");
        },
      }),
    /Unsafe unauthenticated preview URL/,
  );

  assert.deepEqual(authCalls, []);
  assert.deepEqual(fetchCalls, []);
});

test("fetchDocumentText rejects arbitrary same-origin api URLs without authenticated fetch", async () => {
  clearDocumentFetchCaches();
  const authCalls: Array<string | URL | Request> = [];
  const fetchCalls: Array<string | URL | Request> = [];

  await assert.rejects(
    () =>
      fetchDocumentText("/api/chat/stream", {
        authenticatedRequest: async (input) => {
          authCalls.push(input);
          return new Response("unexpected-authenticated-fetch");
        },
        fetchImpl: async (input) => {
          fetchCalls.push(input);
          return new Response("unexpected-native-fetch");
        },
      }),
    /Unsafe unauthenticated preview URL/,
  );

  assert.deepEqual(authCalls, []);
  assert.deepEqual(fetchCalls, []);
});

test("fetchDocumentText uses authenticated fetch for upload file URLs", async () => {
  clearDocumentFetchCaches();
  const authCalls: Array<string | URL | Request> = [];
  const fetchCalls: Array<string | URL | Request> = [];

  const text = await fetchDocumentText("/api/upload/file/report.txt", {
    authenticatedRequest: async (input) => {
      authCalls.push(input);
      return new Response("upload-text");
    },
    fetchImpl: async (input) => {
      fetchCalls.push(input);
      return new Response("unexpected-native-fetch");
    },
  });

  assert.deepEqual(authCalls, ["/api/upload/file/report.txt"]);
  assert.deepEqual(fetchCalls, []);
  assert.equal(text, "upload-text");
});

test("fetchXlsxPreviewJson requires a protected URL and an application/json response", async () => {
  const payload = await fetchXlsxPreviewJson(
    "/api/ai/files/file-1/preview?session_id=session-1&run_id=run-1",
    {
      authenticatedRequest: async () =>
        new Response('{"kind":"xlsx_table"}', {
          headers: { "content-type": "application/json; charset=utf-8" },
        }),
    },
  );
  assert.equal(payload, '{"kind":"xlsx_table"}');

  await assert.rejects(
    () =>
      fetchXlsxPreviewJson("/api/ai/files/file-1/preview", {
        authenticatedRequest: async () =>
          new Response("PK", {
            headers: {
              "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            },
          }),
      }),
    /Unexpected XLSX preview content type/,
  );
  await assert.rejects(
    () => fetchXlsxPreviewJson("https://example.com/book.xlsx"),
    /Unsafe unauthenticated XLSX preview URL/,
  );
});

test("fetchDocumentText does not cache protected platform artifact bytes across auth scope", async () => {
  clearDocumentFetchCaches();
  let authenticatedCount = 0;

  const first = await fetchDocumentText("/api/ai/artifacts/doc/download", {
    authenticatedRequest: async () =>
      new Response(`protected-${++authenticatedCount}`),
  });
  const second = await fetchDocumentText("/api/ai/artifacts/doc/download", {
    authenticatedRequest: async () =>
      new Response(`protected-${++authenticatedCount}`),
  });

  assert.equal(first, "protected-1");
  assert.equal(second, "protected-2");
  assert.equal(authenticatedCount, 2);
});

test("clearAuthState clears document fetch caches when tokens are cleared", async () => {
  clearDocumentFetchCaches();
  const stubs = installAuthBrowserStubs();
  let fetchCount = 0;

  try {
    const first = await fetchDocumentText("/static/cached.txt", {
      fetchImpl: async () => new Response(`public-${++fetchCount}`),
    });
    clearAuthState();
    const second = await fetchDocumentText("/static/cached.txt", {
      fetchImpl: async () => new Response(`public-${++fetchCount}`),
    });

    assert.equal(first, "public-1");
    assert.equal(second, "public-2");
    assert.deepEqual(stubs.events, ["auth:logout"]);
    assert.deepEqual(stubs.removedKeys, [
      "ai_platform_session_present",
      "access_token",
      "refresh_token",
    ]);
  } finally {
    stubs.restore();
    clearDocumentFetchCaches();
  }
});

test("document request selector authenticates only artifact and upload file api URLs", () => {
  assert.equal(
    shouldUseAuthenticatedDocumentRequest(
      "https://app.example.test/api/ai/artifacts/artifact-1/download",
      {
        currentOrigin: "https://app.example.test",
      },
    ),
    true,
  );
  assert.equal(
    shouldUseAuthenticatedDocumentRequest("/api/upload/file/report.txt", {
      currentOrigin: "https://app.example.test",
    }),
    true,
  );
  assert.equal(
    shouldUseAuthenticatedDocumentRequest("/api/users", {
      currentOrigin: "https://app.example.test",
    }),
    false,
  );
  assert.equal(
    shouldUseAuthenticatedDocumentRequest("/api/chat/stream", {
      currentOrigin: "https://app.example.test",
    }),
    false,
  );
  assert.equal(
    shouldUseAuthenticatedDocumentRequest("https://example.com/file.docx", {
      currentOrigin: "https://app.example.test",
    }),
    false,
  );
});
