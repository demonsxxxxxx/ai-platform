import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
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
  createUploadScheduler,
  isDuplicateFileUpload,
  removeAttachmentFromUpload,
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

test("duplicate uploads require the same tracked File object", () => {
  const file = new File(["fixture"], "fixture.txt", {
    type: "text/plain",
    lastModified: 10,
  });
  const distinctFileWithSameMetadata = new File(["fixture"], "fixture.txt", {
    type: "text/plain",
    lastModified: 10,
  });
  const tracked = new Map([["temp", file]]);

  assert.equal(isDuplicateFileUpload(file, tracked), true);
  assert.equal(isDuplicateFileUpload(distinctFileWithSameMetadata, tracked), false);
  assert.equal(
    isDuplicateFileUpload(
      new File(["different"], "other.txt", { type: "text/plain" }),
      new Map(),
    ),
    false,
  );
});

test("upload scheduler starts bounded tasks in FIFO order", async () => {
  const scheduler = createUploadScheduler(2);
  const first = scheduler.reserve();
  const second = scheduler.reserve();
  const third = scheduler.reserve();
  const fourth = scheduler.reserve();
  const started: string[] = [];

  assert.equal(first.waitFor, undefined);
  assert.equal(second.waitFor, undefined);
  assert.ok(third.waitFor);
  assert.ok(fourth.waitFor);
  void third.waitFor.then(() => started.push("third"));
  void fourth.waitFor.then(() => started.push("fourth"));

  first.release();
  await third.waitFor;
  assert.deepEqual(started, ["third"]);

  second.release();
  await fourth.waitFor;
  assert.deepEqual(started, ["third", "fourth"]);
  third.release();
  fourth.release();
});

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

test("queued uploads wait for a scheduler slot before preparing a file", async () => {
  const harness = createHarness();
  const firstStarted = deferred<void>();
  const firstResult = deferred<UploadResult>();
  let prepareCalls = 0;
  let uploadCalls = 0;
  const ids = ["first-temp", "second-temp", "first-final", "second-final"];
  const scheduler = createUploadScheduler(1);
  const firstReservation = scheduler.reserve();
  const secondReservation = scheduler.reserve();

  const first = startFileUploadTask({
    file: harness.file,
    fileCategory: "document",
    t: translate,
    onAttachmentsChange: harness.onAttachmentsChange,
    abortMap: harness.abortMap,
    cancelled: harness.cancelled,
    waitFor: firstReservation.waitFor,
    prepareFile: () => {
      prepareCalls += 1;
      return Promise.resolve(harness.file);
    },
    uploadClient: {
      uploadFile: () => {
        uploadCalls += 1;
        firstStarted.resolve();
        return { promise: firstResult.promise, abort: () => {} };
      },
    },
    createId: () => ids.shift() ?? "unexpected",
    notifyError: (message) => harness.toasts.push(message),
    reportFailure: (error) => harness.reports.push(error),
    onTaskSettled: firstReservation.release,
  });
  const second = startFileUploadTask({
    file: new File(["second"], "second.txt", { type: "text/plain" }),
    fileCategory: "document",
    t: translate,
    onAttachmentsChange: harness.onAttachmentsChange,
    abortMap: harness.abortMap,
    cancelled: harness.cancelled,
    waitFor: secondReservation.waitFor,
    prepareFile: () => {
      prepareCalls += 1;
      return Promise.resolve(harness.file);
    },
    uploadClient: {
      uploadFile: () => {
        uploadCalls += 1;
        return { promise: Promise.resolve(uploadResult()), abort: () => {} };
      },
    },
    createId: () => ids.shift() ?? "unexpected",
    notifyError: (message) => harness.toasts.push(message),
    reportFailure: (error) => harness.reports.push(error),
    onTaskSettled: secondReservation.release,
  });

  await firstStarted.promise;
  assert.equal(prepareCalls, 1);
  assert.equal(uploadCalls, 1);
  assert.equal(harness.attachments.length, 2);
  assert.equal(
    harness.attachments.find((attachment) => attachment.id === "temp-second-temp")
      ?.uploadStatus,
    "queued",
  );

  firstResult.resolve(uploadResult());
  await first.done;
  await second.done;

  assert.equal(prepareCalls, 2);
  assert.equal(uploadCalls, 2);
  assert.equal(harness.attachments.length, 2);
  assert.equal(harness.attachments.every((attachment) => !attachment.isUploading), true);
  assert.deepEqual(harness.toasts, []);
  assert.deepEqual(harness.reports, []);
});

test("upload capacity responses retry without dropping the attachment", async () => {
  const harness = createHarness();
  const delays: number[] = [];
  let uploadCalls = 0;
  const ids = ["retry-temp", "retry-final"];

  const task = startFileUploadTask({
    file: harness.file,
    fileCategory: "document",
    t: translate,
    onAttachmentsChange: harness.onAttachmentsChange,
    abortMap: harness.abortMap,
    cancelled: harness.cancelled,
    prepareFile: () => Promise.resolve(harness.file),
    uploadClient: {
      uploadFile: () => {
        uploadCalls += 1;
        return {
          promise:
            uploadCalls < 3
              ? Promise.reject(
                  new UploadRequestError(
                    "capacity",
                    429,
                    "upload_session_limit_exceeded",
                  ),
                )
              : Promise.resolve(uploadResult()),
          abort: () => {},
        };
      },
    },
    retryDelay: async (attempt) => {
      delays.push(attempt);
    },
    createId: () => ids.shift() ?? "unexpected",
    notifyError: (message) => harness.toasts.push(message),
    reportFailure: (error) => harness.reports.push(error),
  });

  await task.done;

  assert.equal(uploadCalls, 3);
  assert.deepEqual(delays, [0, 1]);
  assert.equal(harness.attachments.length, 1);
  assert.equal(harness.attachments[0]?.isUploading, undefined);
  assert.deepEqual(harness.toasts, []);
  assert.deepEqual(harness.reports, []);
});

test("removing a Composer attachment cancels pending uploads and deletes ready files", async () => {
  const pending: MessageAttachment = {
    id: "temp-pending",
    key: "",
    name: "pending.txt",
    type: "document",
    mimeType: "text/plain",
    size: 1,
    isUploading: true,
  };
  const ready: MessageAttachment = {
    id: "ready-file",
    key: "server-file",
    name: "ready.txt",
    type: "document",
    mimeType: "text/plain",
    size: 1,
  };
  let attachments = [pending, ready];
  const cancelled: string[] = [];
  const deleted: string[] = [];
  const onAttachmentsChange = (
    change:
      | MessageAttachment[]
      | ((previous: MessageAttachment[]) => MessageAttachment[]),
  ) => {
    attachments = typeof change === "function" ? change(attachments) : change;
  };

  removeAttachmentFromUpload(
    pending,
    (id) => {
      cancelled.push(id);
      onAttachmentsChange((previous) =>
        previous.filter((attachment) => attachment.id !== id),
      );
    },
    onAttachmentsChange,
    async (key) => deleted.push(key),
  );
  removeAttachmentFromUpload(
    ready,
    (id) => cancelled.push(id),
    onAttachmentsChange,
    async (key) => deleted.push(key),
  );
  await Promise.resolve();

  assert.deepEqual(cancelled, ["temp-pending"]);
  assert.deepEqual(deleted, ["server-file"]);
  assert.deepEqual(attachments, []);
});

test("page drops and Composer drops use the same upload controller", () => {
  const source = (path: string) => readFileSync(join(process.cwd(), path), "utf8");
  const dragAndDrop = source("src/components/layout/AppContent/useDragAndDrop.ts");
  const appContent = source("src/components/layout/AppContent/ChatAppContent.tsx");
  const chatView = source("src/components/layout/AppContent/ChatView.tsx");
  const chatInput = source("src/components/chat/ChatInput.tsx");
  const uploadHook = source("src/hooks/useFileUpload.ts");

  assert.match(uploadHook, /sharedControls\?: FileUploadControls/);
  assert.match(dragAndDrop, /const uploadControls: FileUploadControls = useFileUpload/);
  assert.match(dragAndDrop, /uploadControls,\s*clearPageDragAttachments: uploadControls\.clearUploads/);
  assert.match(appContent, /uploadControls=\{uploadControls\}/);
  assert.match(chatView, /uploadControls: FileUploadControls/);
  assert.match(chatInput, /sharedControls: sharedUploadControls/);
});

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
  const deleted: string[] = [];
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
    deleteFile: (key) => {
      deleted.push(key);
      return Promise.resolve();
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
  assert.deepEqual(deleted, ["uploaded-key"]);
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

// Mounted adapters use the real controller; only the network and toast sinks are replaced.
test("paste and both drops match central accepted-file admission", async (suite) => {
  const { installSelectTestDom } = await import("../../components/common/__tests__/selectTestDom");
  const fixture = installSelectTestDom();
  const { act, createElement, useRef } = await import("react");
  const { createLobeTestRoot } = await import("../../components/common/__tests__/lobeUiTestRoot");
  const { useDragAndDrop } = await import("../../components/layout/AppContent/useDragAndDrop");
  const { usePasteHandler } = await import("../usePasteHandler");
  const { ChatInput } = await import("../../components/chat/ChatInput");
  const { AuthProvider } = await import("../useAuth");
  const { uploadApi } = await import("../../services/api/upload");
  const { toast } = await import("@lobehub/ui/base-ui");
  const { default: i18n } = await import("../../i18n");
  const { PASTE_TEXT_THRESHOLD } = await import("../../components/chat/chatInputConstants");
  const originalConfig = uploadApi.getConfig, originalUpload = uploadApi.uploadFile;
  const originalError = toast.error, originalInfo = toast.info;
  type Route = "direct" | "paste" | "page" | "composer";
  const routes: Route[] = ["direct", "paste", "page", "composer"];
  const good = () => new File(["ok"], "good.txt", { type: "text/plain" });
  const bad = () => new File(["no"], "bad.exe", { type: "application/octet-stream" });
  async function run({ route, files = [], accepted = [".txt"], maxFiles = 1, current = 0, plain = "", html = "", sizeLimit = 100000, ignored = false }: {
    route: Route; files?: File[]; accepted?: string[]; maxFiles?: number; current?: number;
    plain?: string; html?: string; sizeLimit?: number; ignored?: boolean;
  }) {
    const sent: string[] = [], errors: string[] = [], infos: unknown[] = [];
    let configCalls = 0, changedInput = "", resizes = 0;
    uploadApi.getConfig = async () => { configCalls += 1; return { enabled: true, maxFiles, uploadLimitsBytes: { image: sizeLimit, video: sizeLimit, audio: sizeLimit, document: sizeLimit } }; };
    uploadApi.uploadFile = (file) => { sent.push(file.name); return { promise: Promise.resolve({ key: file.name, url: "", name: file.name, type: file.type.startsWith("image/") ? "image" : "document", mimeType: file.type, size: file.size } as UploadResult), abort() {} }; };
    toast.error = ((value: unknown) => { errors.push(String(value)); }) as typeof toast.error;
    toast.info = ((value: unknown) => { infos.push(value); }) as typeof toast.info;
    let owner!: ReturnType<typeof useDragAndDrop>, paste!: ReturnType<typeof usePasteHandler>;
    const container = document.createElement("div");
    const root = await createLobeTestRoot(container);
    function Harness() {
      owner = useDragAndDrop(accepted);
      const textareaRef = useRef<HTMLTextAreaElement>(null);
      paste = usePasteHandler({ textareaRef, input: "AB", setInput: (value) => { changedInput = value; }, uploadFiles: owner.uploadControls.uploadFiles, validateCount: owner.uploadControls.validateCount, scheduleTextareaResize: () => { resizes += 1; } });
      if (route !== "composer") return createElement("textarea", { ref: textareaRef, defaultValue: "AB" });
      return createElement(AuthProvider, null, createElement(ChatInput, {
        onSend: async () => ({ status: "accepted" as const }), onStop: async () => "unavailable" as const,
        isLoading: false, attachments: owner.pageDragAttachments, onAttachmentsChange: owner.setPageDragAttachments,
        uploadControls: owner.uploadControls, acceptedFileTypes: accepted, disableSlashCommands: true,
        tools: [], skills: [], enableSkills: false, agentOptionValues: {}, availableModels: [],
      }));
    }
    let prevented = false;
    try {
      await act(async () => { root.render(createElement(Harness)); });
      if (current) await act(async () => owner.setPageDragAttachments(Array.from({ length: current }, (_, index): MessageAttachment => ({ id: `existing-${index}`, key: "existing", name: "existing.txt", type: "document", mimeType: "text/plain", size: 1 }))));
      const textarea = container.querySelector("textarea");
      if (textarea) textarea.selectionStart = textarea.selectionEnd = 1;
      await act(async () => {
        if (route === "direct") owner.uploadControls.uploadFiles(files);
        else if (route === "paste") paste.handlePaste({ clipboardData: { files, getData: (type: string) => type === "text/html" ? html : plain }, preventDefault() { prevented = true; } } as unknown as React.ClipboardEvent);
        else {
          const target = route === "composer" ? container.querySelector('[data-disable-global-file-drop="true"]') : document.body;
          assert.ok(target);
          if (ignored) (target as HTMLElement).setAttribute("data-disable-global-file-drop", "true");
          const event = new Event("drop", { bubbles: true, cancelable: true });
          Object.defineProperty(event, "dataTransfer", { value: { files, types: ["Files"], getData: () => "" } });
          target.dispatchEvent(event); prevented = event.defaultPrevented;
          if (ignored) (target as HTMLElement).removeAttribute("data-disable-global-file-drop");
        }
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      assert.equal(configCalls, 1, "Composer reuses the page upload owner");
      return { sent, errors, infos, prevented, changedInput, resizes, attachments: owner.pageDragAttachments.map(({ name, type, isUploading }) => ({ name, type, isUploading })) };
    } finally { await act(async () => root.unmount()); }
  }
  try {
    const scenarios = [
      { name: "mixed batch fits after filtering", files: () => [good(), bad()], expected: ["good.txt"], errors: ["fileUpload.serverUnsupportedFileType"] },
      { name: "accepted batch exceeds count", files: () => [good(), new File(["ok"], "second.txt")], expected: [], errors: ["fileUpload.tooManyFiles"] },
      { name: "existing attachments consume count", files: () => [good(), bad()], current: 1, maxFiles: 2, expected: ["good.txt"], errors: ["fileUpload.serverUnsupportedFileType"] },
      { name: "full owner rejects accepted files", files: () => [good(), bad()], current: 1, expected: [], errors: ["fileUpload.serverUnsupportedFileType", "fileUpload.tooManyFiles"] },
      { name: "no accepted files bypass count", files: () => [bad(), bad()], current: 1, expected: [], errors: ["fileUpload.serverUnsupportedFileType"] },
      { name: "size limit still rejects", files: () => [good()], sizeLimit: 1, expected: [], errors: ["fileUpload.fileTooLarge"] },
      { name: "image wildcard preserves category", files: () => [new File(["png"], "photo.png", { type: "image/png" })], accepted: ["image/*"], expected: ["photo.png"], errors: [] },
    ];
    for (const scenario of scenarios) await suite.test(scenario.name, async (scenarioSuite) => {
      let baseline: Awaited<ReturnType<typeof run>> | undefined;
      for (const route of routes) await scenarioSuite.test(route, async () => {
        const result = await run({ ...scenario, route, files: scenario.files() });
        assert.deepEqual(result.sent, scenario.expected, route);
        assert.equal(result.errors.length, scenario.errors.length, route);
        scenario.errors.forEach((key, index) => assert.ok(result.errors[index].includes(String(i18n.t(key, { count: scenario.maxFiles ?? 1 }))), `${route}: ${key}`));
        if (baseline) { assert.deepEqual(result.attachments, baseline.attachments, route); assert.deepEqual(result.errors, baseline.errors, route); assert.equal(result.prevented, true, route); }
        else baseline = result;
      });
    });
    await suite.test("explicit empty acceptance admits nothing", async () => {
      for (const route of routes) { const result = await run({ route, files: [good()], accepted: [] }); assert.deepEqual(result.sent, []); assert.deepEqual(result.attachments, []); assert.equal(result.errors.length, route === "direct" || route === "paste" ? 1 : 0); }
    });
    await suite.test("global drop respects scoped owner and unmount cleanup", async () => {
      assert.deepEqual((await run({ route: "page", files: [good()], ignored: true })).sent, []);
      const event = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(event, "dataTransfer", { value: { files: [good()], types: ["Files"] } });
      document.body.dispatchEvent(event); assert.equal(event.defaultPrevented, false);
    });
    await suite.test("file clipboard takes precedence over text", async () => {
      const result = await run({ route: "paste", files: [good()], plain: "x".repeat(PASTE_TEXT_THRESHOLD + 1), html: "<p>ignored</p>" });
      assert.deepEqual(result.sent, ["good.txt"]); assert.equal(result.changedInput, ""); assert.deepEqual(result.infos, []);
    });
    await suite.test("short plain text remains native; empty clipboard is inert", async () => {
      const result = await run({ route: "paste", plain: "ordinary text" });
      assert.equal(result.prevented, false); assert.deepEqual(result.sent, []); assert.equal(result.changedInput, "");
      for (const route of routes) assert.deepEqual((await run({ route })).sent, []);
    });
    await suite.test("HTML insertion preserves callbacks and resize", async () => {
      const result = await run({ route: "paste", html: "<p>hello</p>" });
      assert.equal(result.prevented, true); assert.equal(result.changedInput, "AhelloB"); assert.equal(result.resizes, 1); assert.deepEqual(result.sent, []);
    });
    await suite.test("long text retains count guard and conversion info", async () => {
      const plain = "x".repeat(PASTE_TEXT_THRESHOLD + 1);
      const allowed = await run({ route: "paste", plain });
      assert.equal(allowed.sent.length, 1); assert.match(allowed.sent[0], /^clipboard-\d+\.txt$/); assert.equal(allowed.infos.length, 1);
      const full = await run({ route: "paste", plain, current: 1 });
      assert.deepEqual(full.sent, []); assert.equal(full.infos.length, 0); assert.equal(full.errors.length, 1); assert.equal(full.prevented, true);
    });
  } finally {
    uploadApi.getConfig = originalConfig; uploadApi.uploadFile = originalUpload;
    toast.error = originalError; toast.info = originalInfo; fixture.close();
  }
});
