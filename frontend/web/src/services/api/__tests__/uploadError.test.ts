import assert from "node:assert/strict";
import test from "node:test";

import { UploadRequestError, uploadApi } from "../upload.ts";

type XhrOutcome =
  | { type: "load"; status: number; detail: unknown }
  | { type: "error" }
  | { type: "manual" };

function installUploadXhr(outcome: XhrOutcome) {
  const original = Object.getOwnPropertyDescriptor(globalThis, "XMLHttpRequest");

  class UploadRequest {
    status = outcome.type === "load" ? outcome.status : 0;
    statusText = "Internal Server Error";
    responseText =
      outcome.type === "load" ? JSON.stringify({ detail: outcome.detail }) : "";
    withCredentials = false;
    readonly upload = { addEventListener: () => undefined };
    private readonly listeners = new Map<string, Array<(event: Event) => void>>();

    addEventListener(type: string, listener: (event: Event) => void) {
      const listeners = this.listeners.get(type) ?? [];
      listeners.push(listener);
      this.listeners.set(type, listeners);
    }

    open() {}
    setRequestHeader() {}

    abort() {
      queueMicrotask(() => this.emit("abort"));
    }

    send() {
      if (outcome.type !== "manual") {
        queueMicrotask(() => this.emit(outcome.type));
      }
    }

    private emit(type: string) {
      const event = new Event(type);
      for (const listener of this.listeners.get(type) ?? []) {
        listener(event);
      }
    }
  }

  Object.defineProperty(globalThis, "XMLHttpRequest", {
    configurable: true,
    value: UploadRequest as unknown as typeof XMLHttpRequest,
  });

  return () => {
    if (original) {
      Object.defineProperty(globalThis, "XMLHttpRequest", original);
    } else {
      delete (globalThis as { XMLHttpRequest?: typeof XMLHttpRequest })
        .XMLHttpRequest;
    }
  };
}

async function expectUploadError(
  outcome: XhrOutcome,
): Promise<UploadRequestError> {
  const restore = installUploadXhr(outcome);
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
  "upload API classifies the bounded too-large rejection without exposing detail",
  { concurrency: false },
  async () => {
    const error = await expectUploadError({
      type: "load",
      status: 413,
      detail: "file_too_large",
    });

    assert.equal(error.kind, "file_too_large");
    assert.equal(error.status, 413);
    assert.equal(error.code, "file_too_large");
    assert.doesNotMatch(error.message, /file_too_large/);
  },
);

test(
  "upload API classifies the bounded unsupported rejection without exposing detail",
  { concurrency: false },
  async () => {
    const error = await expectUploadError({
      type: "load",
      status: 415,
      detail: "unsupported_file_type",
    });

    assert.equal(error.kind, "unsupported_file_type");
    assert.equal(error.status, 415);
    assert.equal(error.code, "unsupported_file_type");
    assert.doesNotMatch(error.message, /unsupported/i);
  },
);

test(
  "upload API projects unknown response detail and codes to recoverable errors",
  { concurrency: false },
  async () => {
    const errors: UploadRequestError[] = [];
    for (const outcome of [
      {
        type: "load",
        status: 500,
        detail: "upstream secret=do-not-render",
      },
      {
        type: "load",
        status: 500,
        detail: "file_too_large",
      },
      {
        type: "load",
        status: 415,
        detail: { code: "unexpected_format_detail" },
      },
    ] satisfies XhrOutcome[]) {
      errors.push(await expectUploadError(outcome));
    }

    for (const error of errors) {
      assert.equal(error.kind, "recoverable");
      assert.equal(error.code, undefined);
      assert.equal("detail" in error, false);
      assert.doesNotMatch(
        `${error.message} ${JSON.stringify(error)}`,
        /secret|upstream|file_too_large|unexpected_format_detail/i,
      );
    }
  },
);

test(
  "upload API projects network failures to a bounded recoverable error",
  { concurrency: false },
  async () => {
    const error = await expectUploadError({ type: "error" });

    assert.equal(error.kind, "recoverable");
    assert.equal(error.status, undefined);
    assert.equal(error.code, undefined);
    assert.doesNotMatch(error.message, /network/i);
  },
);

test(
  "upload API classifies cancellation while waiting for the access token",
  { concurrency: false },
  async () => {
    const restore = installUploadXhr({ type: "manual" });
    try {
      const handle = uploadApi.uploadFile(
        new File(["fixture"], "fixture.txt", { type: "text/plain" }),
      );
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

test(
  "upload API classifies an in-flight XHR abort as cancelled",
  { concurrency: false },
  async () => {
    const restore = installUploadXhr({ type: "manual" });
    try {
      const handle = uploadApi.uploadFile(
        new File(["fixture"], "fixture.txt", { type: "text/plain" }),
      );
      await Promise.resolve();
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
