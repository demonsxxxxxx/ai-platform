import assert from "node:assert/strict";
import test from "node:test";

import {
  clearDocumentFetchCaches,
  fetchDocumentArrayBuffer,
  fetchDocumentText,
} from "../documentFetchCache.ts";
import {
  downloadPreviewUrl,
  openPreviewUrl,
  resolveDocumentPreviewUrl,
  resolvePptPreviewBuffer,
} from "../documentPreviewSources.ts";
import { assertSafeDocumentPreviewUrl } from "../useDocumentPreviewState.ts";

test("downloadPreviewUrl uses authenticated download for protected platform URLs", async () => {
  const downloadCalls: Array<{ url: string; fileName: string }> = [];
  const fetchCalls: string[] = [];
  const opened: string[] = [];

  await downloadPreviewUrl({
    url: "/api/ai/artifacts/protected-download/download",
    fileName: "protected.docx",
    downloadAuthenticatedFile: async (url, fileName) => {
      downloadCalls.push({ url, fileName });
      return { filename: fileName, objectUrl: "blob:protected-download" };
    },
    fetchImpl: async (input) => {
      fetchCalls.push(String(input));
      return new Response("unexpected-native-fetch");
    },
    openWindow: (url) => {
      opened.push(url);
      return null;
    },
  });

  assert.deepEqual(downloadCalls, [
    {
      url: "/api/ai/artifacts/protected-download/download",
      fileName: "protected.docx",
    },
  ]);
  assert.deepEqual(fetchCalls, []);
  assert.deepEqual(opened, []);
});

test("downloadPreviewUrl uses authenticated download for upload file URLs", async () => {
  const downloadCalls: Array<{ url: string; fileName: string }> = [];
  const fetchCalls: string[] = [];
  const opened: string[] = [];

  await downloadPreviewUrl({
    url: "/api/upload/file/report.docx",
    fileName: "report.docx",
    downloadAuthenticatedFile: async (url, fileName) => {
      downloadCalls.push({ url, fileName });
      return { filename: fileName, objectUrl: "blob:upload-file" };
    },
    fetchImpl: async (input) => {
      fetchCalls.push(String(input));
      return new Response("unexpected-native-fetch");
    },
    openWindow: (url) => {
      opened.push(url);
      return null;
    },
  });

  assert.deepEqual(downloadCalls, [
    {
      url: "/api/upload/file/report.docx",
      fileName: "report.docx",
    },
  ]);
  assert.deepEqual(fetchCalls, []);
  assert.deepEqual(opened, []);
});

test("openPreviewUrl opens protected platform URLs through authenticated blob URLs", async () => {
  const requested: string[] = [];
  const opened: string[] = [];
  const revoked: string[] = [];

  await openPreviewUrl({
    url: "/api/ai/artifacts/protected-preview/preview",
    mimeType: "image/png",
    fetchOptions: {
      authenticatedRequest: async (url) => {
        requested.push(String(url));
        return new Response(new Uint8Array([1, 2, 3]), { status: 200 });
      },
    },
    createObjectURL: (blob) => {
      assert.equal(blob.type, "image/png");
      return "blob:protected-preview";
    },
    revokeObjectURL: (url) => {
      revoked.push(url);
    },
    openWindow: (url) => {
      opened.push(url);
      return null;
    },
    revokeDelayMs: 0,
  });
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.deepEqual(requested, ["/api/ai/artifacts/protected-preview/preview"]);
  assert.deepEqual(opened, ["blob:protected-preview"]);
  assert.deepEqual(revoked, ["blob:protected-preview"]);
});

test("downloadPreviewUrl rejects external signed URL responses", async () => {
  const objectUrls: string[] = [];
  const clicks: string[] = [];
  const opened: string[] = [];
  const revoked: string[] = [];

  await downloadPreviewUrl({
    url: "https://example.com/signed.docx?token=expired",
    fileName: "expired.docx",
    fetchImpl: async () =>
      new Response("<Error><Code>AccessDenied</Code></Error>", {
        status: 403,
      }),
    documentRef: {
      createElement: () => ({
        href: "",
        download: "",
        click() {
          clicks.push(this.href);
        },
      }),
      body: {
        appendChild() {},
        removeChild() {},
      },
    } as unknown as Document,
    createObjectURL: () => {
      const objectUrl = "blob:expired-error";
      objectUrls.push(objectUrl);
      return objectUrl;
    },
    revokeObjectURL: (url) => {
      revoked.push(url);
    },
    openWindow: (url) => {
      opened.push(url);
      return null;
    },
  });

  assert.deepEqual(objectUrls, []);
  assert.deepEqual(clicks, []);
  assert.deepEqual(revoked, []);
  assert.deepEqual(opened, []);
});

test("resolveDocumentPreviewUrl rejects external http preview URLs", async () => {
  const resolved = await resolveDocumentPreviewUrl({
    url: "http://example.com/unsafe.png",
    mimeType: "image/png",
    fetchOptions: {
      currentOrigin: "https://app.example.test",
      authenticatedRequest: async () =>
        new Response("unexpected-authenticated-fetch"),
      fetchImpl: async () => new Response("unexpected-native-fetch"),
    },
    createObjectURL: () => "blob:unexpected",
  });

  assert.equal(resolved, "");
});

test("resolveDocumentPreviewUrl rejects external https preview URLs", async () => {
  const authCalls: string[] = [];
  const fetchCalls: string[] = [];

  const resolved = await resolveDocumentPreviewUrl({
    url: "https://example.com/signed.png?token=external",
    mimeType: "image/png",
    fetchOptions: {
      currentOrigin: "https://app.example.test",
      authenticatedRequest: async (input) => {
        authCalls.push(String(input));
        return new Response("unexpected-authenticated-fetch");
      },
      fetchImpl: async (input) => {
        fetchCalls.push(String(input));
        return new Response("unexpected-native-fetch");
      },
    },
    createObjectURL: () => "blob:unexpected",
  });

  assert.equal(resolved, "");
  assert.deepEqual(authCalls, []);
  assert.deepEqual(fetchCalls, []);
});

test("resolveDocumentPreviewUrl rejects arbitrary authenticated api preview URLs", async () => {
  const authCalls: string[] = [];
  const fetchCalls: string[] = [];

  const resolved = await resolveDocumentPreviewUrl({
    url: "/api/chat/stream",
    mimeType: "application/json",
    fetchOptions: {
      authenticatedRequest: async (input) => {
        authCalls.push(String(input));
        return new Response("unexpected-authenticated-fetch");
      },
      fetchImpl: async (input) => {
        fetchCalls.push(String(input));
        return new Response("unexpected-native-fetch");
      },
    },
    createObjectURL: () => "blob:unexpected",
  });

  assert.equal(resolved, "");
  assert.deepEqual(authCalls, []);
  assert.deepEqual(fetchCalls, []);
});

test("resolveDocumentPreviewUrl rejects encoded internal artifact preview URLs", async () => {
  const authCalls: string[] = [];
  const fetchCalls: string[] = [];

  const resolved = await resolveDocumentPreviewUrl({
    url: "/api/ai/artifacts/%252Eclaude%252Fruns%252Fsecret/download",
    mimeType: "application/pdf",
    fetchOptions: {
      authenticatedRequest: async (input) => {
        authCalls.push(String(input));
        return new Response("unexpected-authenticated-fetch");
      },
      fetchImpl: async (input) => {
        fetchCalls.push(String(input));
        return new Response("unexpected-native-fetch");
      },
    },
    createObjectURL: () => "blob:unexpected",
  });

  assert.equal(resolved, "");
  assert.deepEqual(authCalls, []);
  assert.deepEqual(fetchCalls, []);
});

test("document preview state rejects external http before direct document fetches", () => {
  assert.throws(
    () =>
      assertSafeDocumentPreviewUrl("http://example.com/unsafe.pdf", {
        currentOrigin: "https://app.example.test",
      }),
    /Unsafe external http preview URL/,
  );

  assert.throws(
    () =>
      assertSafeDocumentPreviewUrl("https://example.com/signed.pdf", {
        currentOrigin: "https://app.example.test",
      }),
    /Unsafe unauthenticated preview URL/,
  );
  assert.doesNotThrow(() =>
    assertSafeDocumentPreviewUrl("/api/ai/artifacts/report/download", {
      currentOrigin: "https://app.example.test",
    }),
  );
});

test("document preview state rejects encoded internal artifact URLs before direct document fetches", async () => {
  const encodedInternalUrl =
    "/api/ai/artifacts/%252Eclaude%252Fruns%252Fsecret/download";
  const authCalls: string[] = [];
  const fetchCalls: string[] = [];

  assert.throws(
    () => assertSafeDocumentPreviewUrl(encodedInternalUrl),
    /Unsafe internal preview URL/,
  );

  await assert.rejects(
    fetchDocumentArrayBuffer(encodedInternalUrl, {
      authenticatedRequest: async (input) => {
        authCalls.push(String(input));
        return new Response("unexpected-authenticated-fetch");
      },
      fetchImpl: async (input) => {
        fetchCalls.push(String(input));
        return new Response("unexpected-native-fetch");
      },
    }),
    /Unsafe internal preview URL/,
  );

  assert.deepEqual(authCalls, []);
  assert.deepEqual(fetchCalls, []);
});

test("document preview state rejects encoded internal image URLs before storing image src", () => {
  assert.throws(
    () =>
      assertSafeDocumentPreviewUrl(
        "/api/ai/artifacts/%252Eclaude%252Fruns%252Fsecret/image.png",
      ),
    /Unsafe internal preview URL/,
  );
});

test("document fetch cache rejects external http array buffers without fetching", async () => {
  const fetchCalls: string[] = [];

  await assert.rejects(
    fetchDocumentArrayBuffer("http://example.com/unsafe.pdf", {
      currentOrigin: "https://app.example.test",
      fetchImpl: async (input) => {
        fetchCalls.push(String(input));
        return new Response("unexpected-native-fetch");
      },
    }),
    /Unsafe external http preview URL/,
  );

  assert.deepEqual(fetchCalls, []);
});

test("document fetch cache rejects external http text without fetching", async () => {
  const fetchCalls: string[] = [];

  await assert.rejects(
    fetchDocumentText("http://example.com/unsafe.html", {
      currentOrigin: "https://app.example.test",
      fetchImpl: async (input) => {
        fetchCalls.push(String(input));
        return new Response("unexpected-native-fetch");
      },
    }),
    /Unsafe external http preview URL/,
  );

  assert.deepEqual(fetchCalls, []);
});

test("downloadPreviewUrl rejects external http URLs without fetching or opening", async () => {
  const downloadCalls: Array<{ url: string; fileName: string }> = [];
  const fetchCalls: string[] = [];
  const objectUrls: string[] = [];
  const clicks: string[] = [];
  const opened: string[] = [];

  await downloadPreviewUrl({
    url: "http://example.com/unsafe.docx",
    fileName: "unsafe.docx",
    fetchOptions: {
      currentOrigin: "https://app.example.test",
    },
    downloadAuthenticatedFile: async (url, fileName) => {
      downloadCalls.push({ url, fileName });
      return { filename: fileName, objectUrl: "blob:unexpected-auth" };
    },
    fetchImpl: async (input) => {
      fetchCalls.push(String(input));
      return new Response("unexpected-native-fetch");
    },
    documentRef: {
      createElement: () => ({
        href: "",
        download: "",
        click() {
          clicks.push(this.href);
        },
      }),
      body: {
        appendChild() {},
        removeChild() {},
      },
    } as unknown as Document,
    createObjectURL: () => {
      const objectUrl = "blob:unexpected-native";
      objectUrls.push(objectUrl);
      return objectUrl;
    },
    openWindow: (url) => {
      opened.push(url);
      return null;
    },
  });

  assert.deepEqual(downloadCalls, []);
  assert.deepEqual(fetchCalls, []);
  assert.deepEqual(objectUrls, []);
  assert.deepEqual(clicks, []);
  assert.deepEqual(opened, []);
});

test("downloadPreviewUrl rejects arbitrary same-origin api URLs without fetching or opening", async () => {
  const downloadCalls: Array<{ url: string; fileName: string }> = [];
  const fetchCalls: string[] = [];
  const objectUrls: string[] = [];
  const clicks: string[] = [];
  const opened: string[] = [];

  await downloadPreviewUrl({
    url: "/api/users",
    fileName: "users.json",
    downloadAuthenticatedFile: async (url, fileName) => {
      downloadCalls.push({ url, fileName });
      return { filename: fileName, objectUrl: "blob:unexpected-auth" };
    },
    fetchImpl: async (input) => {
      fetchCalls.push(String(input));
      return new Response("unexpected-native-fetch");
    },
    documentRef: {
      createElement: () => ({
        href: "",
        download: "",
        click() {
          clicks.push(this.href);
        },
      }),
      body: {
        appendChild() {},
        removeChild() {},
      },
    } as unknown as Document,
    createObjectURL: () => {
      const objectUrl = "blob:unexpected-native";
      objectUrls.push(objectUrl);
      return objectUrl;
    },
    openWindow: (url) => {
      opened.push(url);
      return null;
    },
  });

  assert.deepEqual(downloadCalls, []);
  assert.deepEqual(fetchCalls, []);
  assert.deepEqual(objectUrls, []);
  assert.deepEqual(clicks, []);
  assert.deepEqual(opened, []);
});

test("downloadPreviewUrl rejects encoded internal artifact URLs without fetching or opening", async () => {
  const unsafeUrl =
    "/api/ai/artifacts/%252Eclaude%252Fruns%252Fsecret/download";
  const downloadCalls: Array<{ url: string; fileName: string }> = [];
  const fetchCalls: string[] = [];
  const objectUrls: string[] = [];
  const clicks: string[] = [];
  const opened: string[] = [];

  await downloadPreviewUrl({
    url: unsafeUrl,
    fileName: "secret.txt",
    downloadAuthenticatedFile: async (url, fileName) => {
      downloadCalls.push({ url, fileName });
      return { filename: fileName, objectUrl: "blob:unexpected-auth" };
    },
    fetchImpl: async (input) => {
      fetchCalls.push(String(input));
      return new Response("unexpected-native-fetch");
    },
    documentRef: {
      createElement: () => ({
        href: "",
        download: "",
        click() {
          clicks.push(this.href);
        },
      }),
      body: {
        appendChild() {},
        removeChild() {},
      },
    } as unknown as Document,
    createObjectURL: () => {
      const objectUrl = "blob:unexpected-native";
      objectUrls.push(objectUrl);
      return objectUrl;
    },
    openWindow: (url) => {
      opened.push(url);
      return null;
    },
  });

  assert.deepEqual(downloadCalls, []);
  assert.deepEqual(fetchCalls, []);
  assert.deepEqual(objectUrls, []);
  assert.deepEqual(clicks, []);
  assert.deepEqual(opened, []);
});

test("downloadPreviewUrl rejects external https URLs without native fetch", async () => {
  const downloadCalls: Array<{ url: string; fileName: string }> = [];
  const fetchCalls: Array<{
    url: string;
    authorization: string | null;
  }> = [];
  const clicked: string[] = [];
  const revoked: string[] = [];

  await downloadPreviewUrl({
    url: "https://example.com/signed.docx?token=external",
    fileName: "external.docx",
    fetchOptions: {
      currentOrigin: "https://app.example.test",
    },
    downloadAuthenticatedFile: async (url, fileName) => {
      downloadCalls.push({ url, fileName });
      return { filename: fileName, objectUrl: "blob:unexpected-auth" };
    },
    fetchImpl: async (input, init) => {
      fetchCalls.push({
        url: String(input),
        authorization: new Headers(init?.headers).get("Authorization"),
      });
      return new Response("external-docx");
    },
    documentRef: {
      createElement: () => ({
        href: "",
        download: "",
        click() {
          clicked.push(this.href);
        },
      }),
      body: {
        appendChild() {},
        removeChild() {},
      },
    } as unknown as Document,
    createObjectURL: () => "blob:external-docx",
    revokeObjectURL: (url) => {
      revoked.push(url);
    },
  });

  assert.deepEqual(downloadCalls, []);
  assert.deepEqual(fetchCalls, []);
  assert.deepEqual(clicked, []);
  assert.deepEqual(revoked, []);
});

test("resolveDocumentPreviewUrl turns protected media and document URLs into authenticated blob URLs", async () => {
  clearDocumentFetchCaches();
  const authCalls: string[] = [];
  const fetchCalls: string[] = [];
  const objectUrls: string[] = [];
  const protectedCases = [
    { url: "/api/ai/artifacts/video/download", mimeType: "video/mp4" },
    { url: "/api/ai/artifacts/audio/download", mimeType: "audio/mpeg" },
    { url: "/api/ai/artifacts/cad/download", mimeType: "application/dxf" },
    { url: "/api/ai/artifacts/doc/download", mimeType: "application/msword" },
  ];

  for (const item of protectedCases) {
    const resolved = await resolveDocumentPreviewUrl({
      url: item.url,
      mimeType: item.mimeType,
      fetchOptions: {
        authenticatedRequest: async (input) => {
          authCalls.push(String(input));
          return new Response(`${item.mimeType}:${item.url}`);
        },
        fetchImpl: async (input) => {
          fetchCalls.push(String(input));
          return new Response("unexpected-native-fetch");
        },
      },
      createObjectURL: (blob) => {
        const objectUrl = `blob:${blob.type}:${objectUrls.length}`;
        objectUrls.push(objectUrl);
        return objectUrl;
      },
    });

    assert.notEqual(resolved, item.url);
    assert.match(resolved, /^blob:/);
  }

  assert.deepEqual(
    authCalls,
    protectedCases.map((item) => item.url),
  );
  assert.deepEqual(fetchCalls, []);
  assert.equal(objectUrls.length, protectedCases.length);
});

test("resolvePptPreviewBuffer uses authenticated fetch for protected PPT URLs", async () => {
  clearDocumentFetchCaches();
  const authCalls: string[] = [];
  const fetchCalls: string[] = [];

  const buffer = await resolvePptPreviewBuffer({
    url: "/api/ai/artifacts/presentation/download",
    fetchOptions: {
      authenticatedRequest: async (input) => {
        authCalls.push(String(input));
        return new Response("protected-ppt");
      },
      fetchImpl: async (input) => {
        fetchCalls.push(String(input));
        return new Response("unexpected-native-fetch");
      },
    },
  });

  assert.equal(new TextDecoder().decode(buffer), "protected-ppt");
  assert.deepEqual(authCalls, ["/api/ai/artifacts/presentation/download"]);
  assert.deepEqual(fetchCalls, []);
});

test("resolvePptPreviewBuffer rejects external https PPT URLs", async () => {
  clearDocumentFetchCaches();
  const authCalls: string[] = [];
  const fetchCalls: string[] = [];

  await assert.rejects(
    () =>
      resolvePptPreviewBuffer({
        url: "https://example.com/signed.pptx?token=external",
        fetchOptions: {
          authenticatedRequest: async (input) => {
            authCalls.push(String(input));
            return new Response("unexpected-authenticated-fetch");
          },
          fetchImpl: async (input) => {
            fetchCalls.push(String(input));
            return new Response("external-ppt");
          },
        },
      }),
    /Unsafe unauthenticated preview URL/,
  );

  assert.deepEqual(authCalls, []);
  assert.deepEqual(fetchCalls, []);
});

test("resolvePptPreviewBuffer rejects arbitrary same-origin api PPT URLs", async () => {
  clearDocumentFetchCaches();
  const authCalls: string[] = [];
  const fetchCalls: string[] = [];

  await assert.rejects(
    () =>
      resolvePptPreviewBuffer({
        url: "/api/chat/stream",
        fetchOptions: {
          authenticatedRequest: async (input) => {
            authCalls.push(String(input));
            return new Response("unexpected-authenticated-fetch");
          },
          fetchImpl: async (input) => {
            fetchCalls.push(String(input));
            return new Response("unexpected-native-fetch");
          },
        },
      }),
    /Unsafe unauthenticated preview URL/,
  );

  assert.deepEqual(authCalls, []);
  assert.deepEqual(fetchCalls, []);
});

test("resolvePptPreviewBuffer uses authenticated fetch for upload file PPT URLs", async () => {
  clearDocumentFetchCaches();
  const authCalls: string[] = [];
  const fetchCalls: string[] = [];

  const buffer = await resolvePptPreviewBuffer({
    url: "/api/upload/file/presentation.pptx",
    fetchOptions: {
      authenticatedRequest: async (input) => {
        authCalls.push(String(input));
        return new Response("upload-ppt");
      },
      fetchImpl: async (input) => {
        fetchCalls.push(String(input));
        return new Response("unexpected-native-fetch");
      },
    },
  });

  assert.equal(new TextDecoder().decode(buffer), "upload-ppt");
  assert.deepEqual(authCalls, ["/api/upload/file/presentation.pptx"]);
  assert.deepEqual(fetchCalls, []);
});

test("resolvePptPreviewBuffer rejects external http PPT URLs without fetching", async () => {
  clearDocumentFetchCaches();
  const authCalls: string[] = [];
  const fetchCalls: string[] = [];

  await assert.rejects(
    () =>
      resolvePptPreviewBuffer({
        url: "http://example.com/unsafe.pptx",
        fetchOptions: {
          currentOrigin: "https://app.example.test",
          authenticatedRequest: async (input) => {
            authCalls.push(String(input));
            return new Response("unexpected-authenticated-fetch");
          },
          fetchImpl: async (input) => {
            fetchCalls.push(String(input));
            return new Response("unexpected-native-fetch");
          },
        },
      }),
    /Unsafe external http preview URL/,
  );

  assert.deepEqual(authCalls, []);
  assert.deepEqual(fetchCalls, []);
});
