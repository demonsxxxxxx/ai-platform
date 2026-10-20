import type {
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "@assistant-ui/react";
import type {
  FileUploadControls,
  FileUploadHandle,
} from "../../../hooks/useFileUpload";
import type { MessageAttachment } from "../../../types";

export interface PlatformAttachmentBinding {
  /** The existing useFileUpload controller; no parallel adapter state is allowed. */
  uploadControls: Pick<
    FileUploadControls,
    | "uploadFile"
    | "getUploadHandle"
    | "releaseUploadHandle"
    | "cancelUpload"
    | "removeAttachment"
  >;
  acceptedFileTypes?: readonly string[];
  /**
   * Parent-owned full-payload mapping for an already completed upload. This
   * callback must not upload, submit, or put keys/paths/file text in content.
   */
  completeAttachment: (
    attachment: MessageAttachment,
    options?: { signal?: AbortSignal },
  ) => Promise<CompleteAttachment>;
}

function abortError(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("Attachment upload cancelled", "AbortError");
}

function awaitUploadResult(
  handle: FileUploadHandle,
  signal: AbortSignal | undefined,
  cancel: () => void,
): Promise<MessageAttachment> {
  if (signal?.aborted) {
    cancel();
    return Promise.reject(abortError(signal));
  }

  return new Promise((resolve, reject) => {
    let settled = false;
    const onAbort = () => {
      if (settled) return;
      settled = true;
      cancel();
      reject(abortError(signal!));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    void handle.done.then(
      (attachment) => {
        signal?.removeEventListener("abort", onAbort);
        if (settled) return;
        settled = true;
        if (attachment) resolve(attachment);
        else reject(new Error("Attachment upload did not complete"));
      },
      (error) => {
        signal?.removeEventListener("abort", onAbort);
        if (settled) return;
        settled = true;
        reject(error);
      },
    );
  });
}

/**
 * Binds assistant-ui attachment presentation to the existing useFileUpload
 * task handles. Pending IDs are controller temp IDs, so remove/send operate on
 * the controller directly and late success remains fenced by its tombstone.
 */
export function createPlatformAttachmentAdapter(
  binding: PlatformAttachmentBinding,
): AttachmentAdapter {
  const accept = binding.acceptedFileTypes?.join(",") || "*";

  return {
    accept,
    async add({ file }): Promise<PendingAttachment> {
      const handle = binding.uploadControls.uploadFile(file);
      if (!handle) {
        throw new Error("Attachment upload was rejected");
      }
      return {
        id: handle.tempId,
        type: file.type.startsWith("image/") ? "image" : "document",
        name: file.name,
        contentType: file.type,
        file,
        status: { type: "requires-action", reason: "composer-send" },
      };
    },
    async remove(attachment) {
      const handle = binding.uploadControls.getUploadHandle(attachment.id);
      if (!handle) return;
      if (!handle.isSettled()) {
        binding.uploadControls.cancelUpload(attachment.id);
        binding.uploadControls.releaseUploadHandle(attachment.id);
        return;
      }
      const platformAttachment = await handle.done;
      if (platformAttachment) {
        await binding.uploadControls.removeAttachment(platformAttachment);
      }
      binding.uploadControls.releaseUploadHandle(attachment.id);
    },
    async send(attachment, options) {
      const handle = binding.uploadControls.getUploadHandle(attachment.id);
      if (!handle) {
        throw new Error("Attachment upload task is no longer available");
      }
      const platformAttachment = await awaitUploadResult(
        handle,
        options?.signal,
        () => binding.uploadControls.cancelUpload(attachment.id),
      );
      try {
        const completed = await binding.completeAttachment(
          platformAttachment,
          options,
        );
        // Platform full-payload submission owns the attachment reference; the
        // assistant-ui CompleteAttachment carries metadata only in this lane.
        return {
          id: completed.id,
          type: completed.type,
          name: completed.name,
          ...(completed.contentType
            ? { contentType: completed.contentType }
            : {}),
          content: [],
          status: { type: "complete" },
        };
      } finally {
        binding.uploadControls.releaseUploadHandle(attachment.id);
      }
    },
  };
}
