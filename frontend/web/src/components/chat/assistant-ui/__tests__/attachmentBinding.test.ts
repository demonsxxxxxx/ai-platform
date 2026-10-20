import assert from "node:assert/strict";
import test from "node:test";
import type { CompleteAttachment, PendingAttachment } from "@assistant-ui/react";
import type { MessageAttachment, UploadResult } from "../../../../types";
import {
  cancelTemporaryUpload,
  startFileUploadTask,
  type FileUploadHandle,
} from "../../../../hooks/useFileUpload";
import {
  createPlatformAttachmentAdapter,
  type PlatformAttachmentBinding,
} from "../attachmentBinding";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function uploadResult(): UploadResult {
  return {
    key: "synthetic-authorized-key",
    url: "/api/ai/files/synthetic-authorized/download",
    name: "fixture.txt",
    type: "document",
    mimeType: "text/plain",
    size: 7,
  };
}

function createControllerBinding(options: {
  uploadPromise: Promise<UploadResult>;
  onAbort?: () => void;
}) {
  const attachments: MessageAttachment[] = [];
  const handles = new Map<string, FileUploadHandle>();
  const abortMap = new Map<string, () => void>();
  const cancelled = new Set<string>();
  const ids = ["controller-temp", "controller-final"];
  const onAttachmentsChange = (
    change:
      | MessageAttachment[]
      | ((previous: MessageAttachment[]) => MessageAttachment[]),
  ) => {
    const next = typeof change === "function" ? change(attachments) : change;
    attachments.splice(0, attachments.length, ...next);
  };
  const uploadControls: PlatformAttachmentBinding["uploadControls"] = {
    uploadFile: (file) => {
      const task = startFileUploadTask({
        file,
        fileCategory: "document",
        t: (key) => key,
        onAttachmentsChange,
        abortMap,
        cancelled,
        prepareFile: async (source) => source,
        uploadClient: {
          uploadFile: () => ({
            promise: options.uploadPromise,
            abort: () => {
              options.onAbort?.();
            },
          }),
        },
        deleteFile: async () => undefined,
        createId: () => ids.shift() ?? "unexpected",
        notifyError: () => undefined,
        reportFailure: () => undefined,
      });
      handles.set(task.tempId, task);
      return task;
    },
    getUploadHandle: (tempId) => handles.get(tempId),
    releaseUploadHandle: (tempId) => {
      const handle = handles.get(tempId);
      if (!handle) return;
      for (const [id, candidate] of handles) {
        if (candidate === handle) handles.delete(id);
      }
    },
    cancelUpload: (id) => {
      cancelTemporaryUpload(id, abortMap, cancelled, onAttachmentsChange);
    },
    removeAttachment: (attachment) => {
      attachments.splice(
        0,
        attachments.length,
        ...attachments.filter((candidate) => candidate.id !== attachment.id),
      );
    },
  };
  return {
    attachments,
    handles,
    binding: {
      uploadControls,
      completeAttachment: async (): Promise<CompleteAttachment> => ({
        id: "aui-complete",
        type: "document",
        name: "fixture.txt",
        contentType: "text/plain",
        // The adapter strips this content before returning it.
        content: [
          {
            type: "file",
            data: "raw-platform-key",
            mimeType: "text/plain",
            filename: "fixture.txt",
          },
        ],
        status: { type: "complete" },
      }),
    } satisfies PlatformAttachmentBinding,
  };
}

test("adapter add/send uses the real upload task handle and does not duplicate upload", async () => {
  const controller = createControllerBinding({
    uploadPromise: Promise.resolve(uploadResult()),
  });
  const adapter = createPlatformAttachmentAdapter({
    ...controller.binding,
    acceptedFileTypes: [".txt"],
  });
  const file = new File(["fixture"], "fixture.txt", { type: "text/plain" });

  const pending = (await adapter.add({ file })) as PendingAttachment;
  const completed = await adapter.send(pending);

  assert.equal(pending.id, "temp-controller-temp");
  assert.equal(controller.handles.size, 0);
  assert.equal(completed.content.length, 0);
  assert.doesNotMatch(JSON.stringify(completed), /raw-platform-key|synthetic-authorized|api\/ai\/files/i);
  assert.equal(adapter.accept, ".txt");
  assert.equal(controller.attachments[0]?.id, "controller-final");
});

test("adapter remove deletes an upload that completed before removal", async () => {
  const controller = createControllerBinding({
    uploadPromise: Promise.resolve(uploadResult()),
  });
  const adapter = createPlatformAttachmentAdapter(controller.binding);
  const pending = (await adapter.add({
    file: new File(["fixture"], "fixture.txt", { type: "text/plain" }),
  })) as PendingAttachment;

  const handle = controller.handles.get(pending.id);
  assert.ok(handle);
  await handle.done;
  await adapter.remove(pending);

  assert.deepEqual(controller.attachments, []);
});
test("adapter remove cancels the existing task and fences a late upload result", async () => {
  const upload = deferred<UploadResult>();
  let aborts = 0;
  const controller = createControllerBinding({
    uploadPromise: upload.promise,
    onAbort: () => {
      aborts += 1;
    },
  });
  const adapter = createPlatformAttachmentAdapter(controller.binding);
  const file = new File(["fixture"], "fixture.txt", { type: "text/plain" });
  const pending = (await adapter.add({ file })) as PendingAttachment;
  const task = controller.handles.get(pending.id);
  assert.ok(task);

  await adapter.remove(pending);
  upload.resolve(uploadResult());
  await task.done;

  assert.equal(aborts, 1);
  assert.deepEqual(controller.attachments, []);
});
