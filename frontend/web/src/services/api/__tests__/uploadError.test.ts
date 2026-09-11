import assert from "node:assert/strict";
import test from "node:test";

import { UploadRequestError, uploadApi } from "../upload.ts";

type FetchHandler = (
  url: string,
  options: RequestInit,
) => Response | Promise<Response>;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installFetch(handler: FetchHandler): () => void {
  const original = globalThis.fetch;
  globalThis.fetch = ((input: RequestInfo | URL, options: RequestInit = {}) =>
    handler(String(input), options)) as typeof fetch;
  return () => {
    globalThis.fetch = original;
  };
}

async function expectInitiationError(
  status: number,
  detail: unknown,
): Promise<UploadRequestError> {
  const restore = installFetch(() => jsonResponse({ detail }, status));
  try {
    const handle = uploadApi.uploadFile(
      new File(["fixture"], "fixture.txt", { type: "text/plain" }),
    );
    let caught: unknown;
    await assert.rejects(handle.promise, (error: unknown) => {
      caught = error;
      return true;
    });
    assert.ok(caught instanceof UploadRequestError);
    return caught;
  } finally {
    restore();
  }
}

test(
  "upload API classifies bounded server rejections without exposing detail",
  { concurrency: false },
  async () => {
    const tooLarge = await expectInitiationError(413, "file_too_large");
    assert.equal(tooLarge.kind, "file_too_large");
    assert.equal(tooLarge.status, 413);
    assert.equal(tooLarge.code, "file_too_large");
    assert.doesNotMatch(tooLarge.message, /file_too_large/);

    const unsupported = await expectInitiationError(
      415,
      "unsupported_file_type",
    );
    assert.equal(unsupported.kind, "unsupported_file_type");
    assert.equal(unsupported.status, 415);
    assert.equal(unsupported.code, "unsupported_file_type");
    assert.doesNotMatch(unsupported.message, /unsupported/i);

    const capacity = await expectInitiationError(
      429,
      "upload_session_limit_exceeded",
    );
    assert.equal(capacity.kind, "capacity");
    assert.equal(capacity.status, 429);
    assert.equal(capacity.code, "upload_session_limit_exceeded");
  },
);

test(
  "upload API projects unknown response detail and network failures to recoverable errors",
  { concurrency: false },
  async () => {
    const backend = await expectInitiationError(
      500,
      "upstream secret=do-not-render",
    );
    assert.equal(backend.kind, "recoverable");
    assert.equal(backend.status, 500);
    assert.equal(backend.code, undefined);
    assert.doesNotMatch(
      `${backend.message} ${JSON.stringify(backend)}`,
      /secret|upstream/i,
    );

    const restore = installFetch(() => {
      throw new TypeError("network detail");
    });
    try {
      await assert.rejects(
        uploadApi.uploadFile(
          new File(["fixture"], "fixture.txt", { type: "text/plain" }),
        ).promise,
        (error: unknown) =>
          error instanceof UploadRequestError &&
          error.kind === "recoverable" &&
          !/network/i.test(error.message),
      );
    } finally {
      restore();
    }
  },
);

test(
  "all accepted file sizes use one upload session path",
  { concurrency: false },
  async () => {
    const urls: string[] = [];
    const progress: Array<[number, number, number]> = [];
    const restore = installFetch((url) => {
      urls.push(url);
      if (url.endsWith("/api/ai/files/uploads")) {
        return jsonResponse({
          upload_session_id: "upload-1",
          part_size_bytes: 8,
          parts: [{ part_number: 1, url: "/part/1" }],
        });
      }
      if (url === "/part/1") return jsonResponse({ etag: "etag-1" });
      if (url.endsWith("/complete")) {
        return jsonResponse({
          file_id: "file-1",
          name: "small.txt",
          sha256: "abc",
          size_bytes: 1,
        });
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    try {
      const result = await uploadApi.uploadFile(
        new File(["x"], "small.txt", { type: "text/plain" }),
        {
          onProgress: (percent, loaded, total) =>
            progress.push([percent, loaded, total]),
        },
      ).promise;

      assert.equal(result.key, "file-1");
      assert.deepEqual(progress, [[100, 1, 1]]);
      assert.equal(urls.some((url) => url.includes("/api/upload/file")), false);
    } finally {
      restore();
    }
  },
);

test(
  "multipart completion retries the same durable identity and body",
  { concurrency: false },
  async () => {
    const completionBodies: Array<BodyInit | null | undefined> = [];
    const restore = installFetch((url, options) => {
      if (url.endsWith("/api/ai/files/uploads")) {
        return jsonResponse({
          upload_session_id: "upload-retry",
          part_size_bytes: 8,
          parts: [{ part_number: 1, url: "/part/retry" }],
        });
      }
      if (url === "/part/retry") return jsonResponse({ etag: "etag-retry" });
      if (url.endsWith("/complete")) {
        completionBodies.push(options.body);
        if (completionBodies.length < 3) {
          return jsonResponse({ detail: "storage_unavailable" }, 503);
        }
        return jsonResponse({
          file_id: "file-retry",
          name: "retry.txt",
          sha256: "def",
          size_bytes: 1,
        });
      }
      if (url.endsWith("/abort")) return new Response(null, { status: 204 });
      throw new Error(`unexpected URL: ${url}`);
    });
    try {
      const result = await uploadApi.uploadFile(
        new File(["x"], "retry.txt", { type: "text/plain" }),
      ).promise;

      assert.equal(result.key, "file-retry");
      assert.equal(completionBodies.length, 3);
      assert.equal(new Set(completionBodies).size, 1);
    } finally {
      restore();
    }
  },
);

test(
  "upload API classifies an in-flight session abort as cancelled",
  { concurrency: false },
  async () => {
    let requestStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      requestStarted = resolve;
    });
    const restore = installFetch((_url, options) => {
      requestStarted();
      return new Promise<Response>((_resolve, reject) => {
        options.signal?.addEventListener("abort", () =>
          reject(new DOMException("aborted", "AbortError")),
        );
      });
    });
    try {
      const handle = uploadApi.uploadFile(
        new File(["fixture"], "fixture.txt", { type: "text/plain" }),
      );
      await started;
      handle.abort();
      await assert.rejects(
        handle.promise,
        (error: unknown) =>
          error instanceof UploadRequestError && error.kind === "cancelled",
      );
    } finally {
      restore();
    }
  },
);
