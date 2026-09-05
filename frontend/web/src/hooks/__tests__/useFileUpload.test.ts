import assert from "node:assert/strict";
import test from "node:test";

import { UploadRequestError } from "../../services/api/upload.ts";
import type {
  MessageAttachment,
  UploadConfig,
  UploadResult,
} from "../../types";
import {
  cancelTemporaryUpload,
  clearAttachmentResources,
  settleUploadFailure,
  startFileUploadTask,
} from "../useFileUpload.ts";
import {
  formatUploadLimitMiB,
  isFileSizeWithinLimitBytes,
  resolveUploadBytePolicy,
} from "../../utils/uploadLimits.ts";

const translate = (key: string) => `translated:${key}`;
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

test("upload policy prefers the explicit byte contract and supports the byte-valued legacy alias", () => {
  assert.equal(resolveUploadBytePolicy(null), null);
  assert.equal(resolveUploadBytePolicy(undefined), null);
  assert.equal(
    resolveUploadBytePolicy("invalid-json-shape" as unknown as UploadConfig),
    null,
  );

  const explicit = resolveUploadBytePolicy({
    enabled: true,
    uploadLimitsBytes: {
      image: MAX_UPLOAD_BYTES,
      video: MAX_UPLOAD_BYTES,
      audio: MAX_UPLOAD_BYTES,
      document: MAX_UPLOAD_BYTES,
    },
    maxFiles: 10,
    uploadLimits: {
      image: 1,
      video: 1,
      audio: 1,
      document: 1,
      maxFiles: 1,
    },
  });
  assert.deepEqual(explicit, {
    limitsBytes: {
      image: MAX_UPLOAD_BYTES,
      video: MAX_UPLOAD_BYTES,
      audio: MAX_UPLOAD_BYTES,
      document: MAX_UPLOAD_BYTES,
    },
    maxFiles: 10,
  });

  const legacy = resolveUploadBytePolicy({
    enabled: true,
    uploadLimits: {
      image: MAX_UPLOAD_BYTES,
      video: MAX_UPLOAD_BYTES,
      audio: MAX_UPLOAD_BYTES,
      document: MAX_UPLOAD_BYTES,
      maxFiles: 10,
    },
  });
  assert.deepEqual(legacy, explicit);

  const explicitLimitsWithLegacyCount = resolveUploadBytePolicy({
    enabled: true,
    uploadLimitsBytes: explicit?.limitsBytes,
    uploadLimits: {
      image: 1,
      video: 1,
      audio: 1,
      document: 1,
      maxFiles: 7,
    },
  });
  assert.deepEqual(explicitLimitsWithLegacyCount, {
    limitsBytes: explicit?.limitsBytes,
    maxFiles: 7,
  });

  const legacyLimitsWithExplicitCount = resolveUploadBytePolicy({
    enabled: true,
    maxFiles: 9,
    uploadLimits: {
      image: MAX_UPLOAD_BYTES,
      video: MAX_UPLOAD_BYTES,
      audio: MAX_UPLOAD_BYTES,
      document: MAX_UPLOAD_BYTES,
      maxFiles: 1,
    },
  });
  assert.deepEqual(legacyLimitsWithExplicitCount, {
    limitsBytes: explicit?.limitsBytes,
    maxFiles: 9,
  });

  const partialExplicitWire = {
    enabled: true,
    uploadLimitsBytes: {
      image: 2,
    },
    maxFiles: Number.NaN,
    uploadLimits: {
      image: MAX_UPLOAD_BYTES,
      video: MAX_UPLOAD_BYTES,
      audio: MAX_UPLOAD_BYTES,
      document: MAX_UPLOAD_BYTES,
      maxFiles: 4,
    },
  } as unknown as UploadConfig;
  assert.deepEqual(resolveUploadBytePolicy(partialExplicitWire), {
    limitsBytes: {
      image: 2,
      video: MAX_UPLOAD_BYTES,
      audio: MAX_UPLOAD_BYTES,
      document: MAX_UPLOAD_BYTES,
    },
    maxFiles: 4,
  });
});

test("upload size validation compares bytes at the exact boundary and formats MiB only for display", () => {
  assert.equal(
    isFileSizeWithinLimitBytes(MAX_UPLOAD_BYTES, MAX_UPLOAD_BYTES),
    true,
  );
  assert.equal(
    isFileSizeWithinLimitBytes(MAX_UPLOAD_BYTES + 1, MAX_UPLOAD_BYTES),
    false,
  );
  assert.equal(formatUploadLimitMiB(MAX_UPLOAD_BYTES), "50 MiB");
  assert.equal(formatUploadLimitMiB(1.5 * 1024 * 1024), "1.5 MiB");
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function uploadResult(): UploadResult {
  return {
    key: "uploaded-key",
    url: "https://files.example/uploaded-key",
    name: "uploaded.txt",
    type: "document",
    mimeType: "text/plain",
    size: 7,
  };
}

function createHarness() {
  const attachments: MessageAttachment[] = [];
  const abortMap = new Map<string, () => void>();
  const cancelled = new Set<string>();
  const toasts: string[] = [];
  const reports: unknown[] = [];
  const state = { updates: 0, removals: 0 };
  const onAttachmentsChange = (
    change:
      | MessageAttachment[]
      | ((previous: MessageAttachment[]) => MessageAttachment[]),
  ) => {
    const next = typeof change === "function" ? change(attachments) : change;
    state.updates += 1;
    if (next.length < attachments.length) {
      state.removals += 1;
    }
    attachments.splice(0, attachments.length, ...next);
  };

  return {
    file: new File(["fixture"], "fixture.txt", { type: "text/plain" }),
    attachments,
    abortMap,
    cancelled,
    toasts,
    reports,
    state,
    onAttachmentsChange,
  };
}

test("clearing attachment resources cancels uploads and deletes completed unbound files", () => {
  const cancelled: string[] = [];
  const deleted: string[] = [];
  const attachments: MessageAttachment[] = [
    {
      id: "temp-upload",
      key: "",
      name: "pending.txt",
      type: "document",
      mimeType: "text/plain",
      size: 1,
      isUploading: true,
    },
    {
      id: "ready-upload",
      key: "file-ready",
      name: "ready.txt",
      type: "document",
      mimeType: "text/plain",
      size: 1,
    },
  ];

  clearAttachmentResources(
    attachments,
    (id) => cancelled.push(id),
    (key) => {
      deleted.push(key);
      return Promise.resolve();
    },
  );

  assert.deepEqual(cancelled, ["temp-upload"]);
  assert.deepEqual(deleted, ["file-ready"]);
});

test("upload failures use bounded copy and remove only the matching temporary attachment", () => {
  const cases = [
    {
      error: new UploadRequestError("file_too_large", 413, "file_too_large"),
      expected: "translated:fileUpload.serverFileTooLarge",
    },
    {
      error: new UploadRequestError(
        "unsupported_file_type",
        415,
        "unsupported_file_type",
      ),
      expected: "translated:fileUpload.serverUnsupportedFileType",
    },
    {
      error: new UploadRequestError("recoverable", 500),
      expected: "translated:fileUpload.uploadFailedRecoverable",
    },
    {
      error: new Error("backend detail must not reach a toast"),
      expected: "translated:fileUpload.uploadFailedRecoverable",
    },
  ];

  for (const { error, expected } of cases) {
    let attachmentIds = ["temp-target", "existing-attachment"];
    let cleanupCalls = 0;
    const message = settleUploadFailure(error, translate, () => {
      cleanupCalls += 1;
      attachmentIds = attachmentIds.filter((id) => id !== "temp-target");
    });

    assert.equal(message, expected);
    assert.equal(cleanupCalls, 1);
    assert.deepEqual(attachmentIds, ["existing-attachment"]);
  }
});

test("cancelled upload failures are silent and do not mutate removed attachments", () => {
  let attachmentIds = ["existing-attachment"];
  let cleanupCalls = 0;
  const message = settleUploadFailure(
    new UploadRequestError("cancelled"),
    translate,
    () => {
      cleanupCalls += 1;
      attachmentIds = attachmentIds.filter((id) => id !== "temp-target");
    },
  );

  assert.equal(message, null);
  assert.equal(cleanupCalls, 0);
  assert.deepEqual(attachmentIds, ["existing-attachment"]);
});

test("cancellation during compression tombstones the temporary attachment before upload", async () => {
  const harness = createHarness();
  const prepareEntered = deferred<void>();
  const prepared = deferred<File>();
  let uploadCalls = 0;
  const task = startFileUploadTask({
    file: harness.file,
    fileCategory: "image",
    t: translate,
    onAttachmentsChange: harness.onAttachmentsChange,
    abortMap: harness.abortMap,
    cancelled: harness.cancelled,
    prepareFile: () => {
      prepareEntered.resolve();
      return prepared.promise;
    },
    uploadClient: {
      uploadFile: () => {
        uploadCalls += 1;
        return { promise: Promise.resolve(uploadResult()), abort: () => {} };
      },
    },
    createId: () => "compression",
    notifyError: (message) => harness.toasts.push(message),
    reportFailure: (error) => harness.reports.push(error),
  });

  await prepareEntered.promise;
  assert.equal(harness.attachments.length, 1);
  cancelTemporaryUpload(
    task.tempId,
    harness.abortMap,
    harness.cancelled,
    harness.onAttachmentsChange,
  );
  assert.equal(harness.cancelled.has(task.tempId), true);
  prepared.resolve(harness.file);
  await task.done;

  assert.equal(uploadCalls, 0);
  assert.equal(harness.attachments.length, 0);
  assert.equal(harness.state.removals, 1);
  assert.deepEqual(harness.toasts, []);
  assert.deepEqual(harness.reports, []);
  assert.equal(harness.abortMap.size, 0);
  assert.equal(harness.cancelled.size, 0);
});

test("active XHR cancellation is idempotent and fences stale progress and results", async () => {
  const harness = createHarness();
  const uploadEntered = deferred<void>();
  const result = deferred<UploadResult>();
  let onProgress: ((progress: number) => void) | undefined;
  let aborts = 0;
  const task = startFileUploadTask({
    file: harness.file,
    fileCategory: "document",
    t: translate,
    onAttachmentsChange: harness.onAttachmentsChange,
    abortMap: harness.abortMap,
    cancelled: harness.cancelled,
    prepareFile: () => Promise.resolve(harness.file),
    uploadClient: {
      uploadFile: (_file, options) => {
        onProgress = options.onProgress;
        uploadEntered.resolve();
        return {
          promise: result.promise,
          abort: () => {
            aborts += 1;
          },
        };
      },
    },
    createId: (() => {
      const ids = ["active-temp", "active-final"];
      return () => ids.shift() ?? "unexpected";
    })(),
    notifyError: (message) => harness.toasts.push(message),
    reportFailure: (error) => harness.reports.push(error),
  });

  await uploadEntered.promise;
  assert.ok(onProgress);
  cancelTemporaryUpload(
    task.tempId,
    harness.abortMap,
    harness.cancelled,
    harness.onAttachmentsChange,
  );
  const updatesAfterCancel = harness.state.updates;
  cancelTemporaryUpload(
    task.tempId,
    harness.abortMap,
    harness.cancelled,
    harness.onAttachmentsChange,
  );
  onProgress(75);
  result.resolve(uploadResult());
  await task.done;

  assert.equal(aborts, 1);
  assert.equal(harness.state.updates, updatesAfterCancel);
  assert.equal(harness.state.removals, 1);
  assert.equal(harness.attachments.length, 0);
  assert.deepEqual(harness.toasts, []);
  assert.deepEqual(harness.reports, []);
  assert.equal(harness.abortMap.size, 0);
  assert.equal(harness.cancelled.size, 0);
});

test("compression fallback continues through normal progress and success", async () => {
  const harness = createHarness();
  const uploadEntered = deferred<void>();
  const result = deferred<UploadResult>();
  let progress: ((value: number) => void) | undefined;
  let fallbackCount = 0;
  const task = startFileUploadTask({
    file: harness.file,
    fileCategory: "image",
    t: translate,
    onAttachmentsChange: harness.onAttachmentsChange,
    abortMap: harness.abortMap,
    cancelled: harness.cancelled,
    prepareFile: async () => {
      try {
        throw new Error("compression failed");
      } catch {
        fallbackCount += 1;
        return harness.file;
      }
    },
    uploadClient: {
      uploadFile: (_file, options) => {
        progress = options.onProgress;
        uploadEntered.resolve();
        return { promise: result.promise, abort: () => {} };
      },
    },
    createId: (() => {
      const ids = ["fallback-temp", "fallback-final"];
      return () => ids.shift() ?? "unexpected";
    })(),
    notifyError: (message) => harness.toasts.push(message),
    reportFailure: (error) => harness.reports.push(error),
  });

  await uploadEntered.promise;
  assert.ok(progress);
  progress(55);
  assert.equal(harness.attachments[0]?.uploadProgress, 55);
  result.resolve(uploadResult());
  await task.done;

  assert.equal(fallbackCount, 1);
  assert.equal(harness.attachments.length, 1);
  assert.equal(harness.attachments[0]?.id, "fallback-final");
  assert.equal(harness.attachments[0]?.key, "uploaded-key");
  assert.deepEqual(harness.toasts, []);
  assert.deepEqual(harness.reports, []);
  assert.equal(harness.abortMap.size, 0);
  assert.equal(harness.cancelled.size, 0);
});
