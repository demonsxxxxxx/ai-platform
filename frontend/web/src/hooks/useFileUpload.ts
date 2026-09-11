import { useState, useCallback, useRef, useEffect } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { uploadApi, UploadRequestError } from "../services/api/upload";
import { compressImageFile } from "../utils/imageCompression";
import {
  formatUploadLimitMiB,
  isFileSizeWithinLimitBytes,
  resolveUploadBytePolicy,
  type ResolvedUploadBytePolicy,
} from "../utils/uploadLimits";
import { uuid } from "../utils/uuid";
import type { MessageAttachment, FileCategory, UploadResult } from "../types";

export interface FileUploadControls {
  uploadLimitsBytes: ResolvedUploadBytePolicy["limitsBytes"] | null;
  uploadFiles: (files: FileList | File[], category?: FileCategory) => void;
  uploadFile: (file: File, category?: FileCategory) => void;
  validateSize: (file: File, category: FileCategory) => boolean;
  validateCount: (newFileCount: number) => boolean;
  cancelUpload: (id: string) => void;
  clearUploads: () => void;
  removeAttachment: (attachment: MessageAttachment) => void;
}

export interface UseFileUploadOptions {
  attachments: MessageAttachment[];
  onAttachmentsChange: (
    attachments:
      | MessageAttachment[]
      | ((prev: MessageAttachment[]) => MessageAttachment[]),
  ) => void;
  acceptedFileTypes?: readonly string[];
  sharedControls?: FileUploadControls;
}

type UploadTranslation = (key: string) => unknown;

/** Project bounded upload failures to product copy and clean failed temporary attachments. */
export function settleUploadFailure(
  error: unknown,
  t: UploadTranslation,
  removeTemporaryAttachment: () => void,
): string | null {
  if (error instanceof UploadRequestError && error.kind === "cancelled") {
    return null;
  }

  const key =
    error instanceof UploadRequestError && error.kind === "file_too_large"
      ? "fileUpload.serverFileTooLarge"
      : error instanceof UploadRequestError &&
          error.kind === "unsupported_file_type"
        ? "fileUpload.serverUnsupportedFileType"
        : "fileUpload.uploadFailedRecoverable";
  removeTemporaryAttachment();
  return String(t(key));
}

function getFileCategory(file: File): FileCategory {
  const type = file.type.toLowerCase();
  if (type.startsWith("image/")) return "image";
  if (type.startsWith("video/")) return "video";
  if (type.startsWith("audio/")) return "audio";
  return "document";
}

type UploadClient = {
  uploadFile: (
    file: File,
    options: { onProgress?: (progress: number) => void },
  ) => ReturnType<typeof uploadApi.uploadFile>;
};

export function isAcceptedProfileFile(
  file: Pick<File, "name" | "type">,
  acceptedFileTypes: readonly string[] | undefined,
): boolean {
  if (acceptedFileTypes === undefined) return true;
  const normalizedType = file.type.toLowerCase();
  const normalizedName = file.name.toLowerCase();
  return acceptedFileTypes.some((entry) => {
    const candidate = entry.trim().toLowerCase();
    if (!candidate) return false;
    if (candidate.startsWith(".")) return normalizedName.endsWith(candidate);
    if (candidate.endsWith("/*")) {
      return normalizedType.startsWith(candidate.slice(0, -1));
    }
    return normalizedType === candidate;
  });
}

export function partitionAcceptedProfileFiles(
  files: readonly File[],
  acceptedFileTypes: readonly string[] | undefined,
): { accepted: File[]; rejected: File[] } {
  const accepted: File[] = [];
  const rejected: File[] = [];
  for (const file of files) {
    (isAcceptedProfileFile(file, acceptedFileTypes) ? accepted : rejected).push(file);
  }
  return { accepted, rejected };
}

interface UploadSlotReservation {
  waitFor?: Promise<void>;
  release: () => void;
}

export function createUploadScheduler(initialLimit: number) {
  let limit = Math.max(1, initialLimit);
  let active = 0;
  const queued: Array<() => void> = [];

  const drain = () => {
    while (active < limit && queued.length > 0) {
      active += 1;
      queued.shift()?.();
    }
  };

  return {
    reserve(): UploadSlotReservation {
      let released = false;
      let waitFor: Promise<void> | undefined;
      if (active < limit) {
        active += 1;
      } else {
        waitFor = new Promise<void>((resolve) => queued.push(resolve));
      }
      return {
        waitFor,
        release: () => {
          if (released) return;
          released = true;
          active -= 1;
          drain();
        },
      };
    },
    setLimit(nextLimit: number) {
      limit = Math.max(1, nextLimit);
      drain();
    },
  };
}

interface FileUploadTaskOptions {
  file: File;
  fileCategory: FileCategory;
  t: UploadTranslation;
  onAttachmentsChange: UseFileUploadOptions["onAttachmentsChange"];
  abortMap: Map<string, () => void>;
  cancelled: Set<string>;
  prepareFile: (file: File) => Promise<File>;
  uploadClient: UploadClient;
  deleteFile?: (key: string) => Promise<unknown>;
  createId: () => string;
  notifyError: (message: string) => void;
  reportFailure: (error: unknown) => void;
  retryDelay?: (attempt: number) => Promise<void>;
  waitFor?: Promise<void>;
  onAttachmentReplaced?: (temporaryId: string, finalId: string) => void;
  onTaskSettled?: (temporaryId: string) => void;
}

interface FileUploadTask {
  tempId: string;
  done: Promise<void>;
}

export function isDuplicateFileUpload(
  file: File,
  uploadSources: ReadonlyMap<string, File>,
): boolean {
  return Array.from(uploadSources.values()).includes(file);
}

/**
 * Tombstones a temporary attachment before removal. The tombstone remains until
 * its task reaches a terminal path, so paused async phases cannot resume it.
 */
export function cancelTemporaryUpload(
  id: string,
  abortMap: Map<string, () => void>,
  cancelled: Set<string>,
  onAttachmentsChange: UseFileUploadOptions["onAttachmentsChange"],
): void {
  const isTemporary = id.startsWith("temp-");
  if (isTemporary && cancelled.has(id)) {
    return;
  }
  if (isTemporary) {
    cancelled.add(id);
  }

  const abort = abortMap.get(id);
  if (abort) {
    abort();
    abortMap.delete(id);
  }
  onAttachmentsChange((previous) =>
    previous.filter((attachment) => attachment.id !== id),
  );
}

export function removeAttachmentFromUpload(
  attachment: MessageAttachment,
  cancelUpload: (id: string) => void,
  onAttachmentsChange: UseFileUploadOptions["onAttachmentsChange"],
  deleteFile: (key: string) => Promise<unknown> = uploadApi.deleteFile,
): void {
  if (attachment.isUploading) {
    cancelUpload(attachment.id);
    return;
  }

  onAttachmentsChange((previous) =>
    previous.filter((item) => item.id !== attachment.id),
  );
  if (attachment.key) {
    void deleteFile(attachment.key).catch((error) => {
      console.error("Failed to delete file from server:", error);
    });
  }
}

/** Owns the full lifecycle of one temporary upload attachment. */
export function startFileUploadTask({
  file,
  fileCategory,
  t,
  onAttachmentsChange,
  abortMap,
  cancelled,
  prepareFile,
  uploadClient,
  deleteFile = uploadApi.deleteFile,
  createId,
  notifyError,
  reportFailure,
  retryDelay = (attempt) =>
    new Promise((resolve) =>
      setTimeout(resolve, 500 * 2 ** attempt + Math.random() * 250),
    ),
  waitFor,
  onAttachmentReplaced,
  onTaskSettled,
}: FileUploadTaskOptions): FileUploadTask {
  const tempId = `temp-${createId()}`;
  const isCancelled = () => cancelled.has(tempId);
  const finish = () => {
    abortMap.delete(tempId);
    cancelled.delete(tempId);
    onTaskSettled?.(tempId);
  };

  const tempAttachment: MessageAttachment = {
    id: tempId,
    key: "",
    name: file.name,
    type: fileCategory,
    mimeType: file.type,
    size: file.size,
    url: "",
    uploadProgress: 0,
    uploadStatus: waitFor ? "queued" : "uploading",
    isUploading: true,
  };
  onAttachmentsChange((previous) => [...previous, tempAttachment]);

  const done = (async () => {
    try {
      if (waitFor) {
        await waitFor;
      }
      if (isCancelled()) {
        finish();
        return;
      }
      onAttachmentsChange((previous) =>
        previous.map((attachment) =>
          attachment.id === tempId
            ? { ...attachment, uploadStatus: "uploading" }
            : attachment,
        ),
      );
      const processedFile = await prepareFile(file);
      if (isCancelled()) {
        finish();
        return;
      }
      onAttachmentsChange((previous) =>
        previous.map((attachment) =>
          attachment.id === tempId
            ? {
                ...attachment,
                name: processedFile.name,
                mimeType: processedFile.type,
                size: processedFile.size,
              }
            : attachment,
        ),
      );

      let result: UploadResult | undefined;
      for (let attempt = 0; attempt < 4; attempt += 1) {
        const handle = uploadClient.uploadFile(processedFile, {
          onProgress: (progress) => {
            if (isCancelled()) {
              return;
            }
            onAttachmentsChange((previous) =>
              previous.map((attachment) =>
                attachment.id === tempId
                  ? {
                      ...attachment,
                      uploadProgress: progress,
                      isUploading: true,
                    }
                  : attachment,
              ),
            );
          },
        });
        abortMap.set(tempId, handle.abort);
        try {
          result = await handle.promise;
          break;
        } catch (error) {
          abortMap.delete(tempId);
          if (
            !(error instanceof UploadRequestError) ||
            error.kind !== "capacity" ||
            attempt === 3 ||
            isCancelled()
          ) {
            throw error;
          }
          onAttachmentsChange((previous) =>
            previous.map((attachment) =>
              attachment.id === tempId
                ? { ...attachment, uploadStatus: "retrying" }
                : attachment,
            ),
          );
          await retryDelay(attempt);
          if (isCancelled()) {
            finish();
            return;
          }
          onAttachmentsChange((previous) =>
            previous.map((attachment) =>
              attachment.id === tempId
                ? { ...attachment, uploadStatus: "uploading" }
                : attachment,
            ),
          );
        }
      }
      if (!result) {
        throw new UploadRequestError("recoverable");
      }
      if (isCancelled()) {
        try {
          await deleteFile(result.key);
        } catch (error) {
          reportFailure(error);
        }
        finish();
        return;
      }
      const finalId = createId();
      const finalAttachment: MessageAttachment = {
        id: finalId,
        key: result.key,
        name: result.name || processedFile.name,
        type: result.type as FileCategory,
        mimeType: result.mimeType,
        size: result.size,
        url: result.url,
      };
      onAttachmentsChange((previous) =>
        previous.map((attachment) =>
          attachment.id === tempId ? finalAttachment : attachment,
        ),
      );
      onAttachmentReplaced?.(tempId, finalId);
      finish();
    } catch (error) {
      abortMap.delete(tempId);
      if (isCancelled()) {
        finish();
        return;
      }
      const message = settleUploadFailure(error, t, () => {
        onAttachmentsChange((previous) =>
          previous.filter((attachment) => attachment.id !== tempId),
        );
      });
      if (message !== null) {
        reportFailure(error);
        notifyError(message);
      }
      finish();
    }
  })();

  return { tempId, done };
}

export function clearAttachmentResources(
  attachments: readonly MessageAttachment[],
  cancelUpload: (id: string) => void,
  deleteFile: (key: string) => Promise<unknown> = uploadApi.deleteFile,
): void {
  for (const attachment of attachments) {
    if (attachment.isUploading) {
      cancelUpload(attachment.id);
    } else if (attachment.key) {
      void deleteFile(attachment.key).catch(() => undefined);
    }
  }
}

export function useFileUpload({
  attachments,
  onAttachmentsChange,
  acceptedFileTypes,
  sharedControls,
}: UseFileUploadOptions) {
  const { t } = useTranslation();
  const [uploadPolicy, setUploadPolicy] =
    useState<ResolvedUploadBytePolicy | null>(null);
  const limitsFetched = useRef(false);
  const abortMapRef = useRef<Map<string, () => void>>(new Map());
  const cancelledUploadIdsRef = useRef<Set<string>>(new Set());
  const uploadSchedulerRef = useRef(createUploadScheduler(1));
  const uploadSourcesRef = useRef<Map<string, File>>(new Map());

  // Fetch upload limits once
  useEffect(() => {
    if (sharedControls || limitsFetched.current) {
      return;
    }

    limitsFetched.current = true;
    let isMounted = true;

    uploadApi
      .getConfig()
      .then((config) => {
        if (isMounted) {
          setUploadPolicy(resolveUploadBytePolicy(config));
          uploadSchedulerRef.current.setLimit(
            config.maxActiveUploadSessions ?? 1,
          );
        }
      })
      .catch(() => {});

    return () => {
      isMounted = false;
    };
  }, [sharedControls]);

  /** Validate file size, returns true if ok */
  const validateSize = useCallback(
    (file: File, category: FileCategory): boolean => {
      if (!uploadPolicy) return true;
      const maxBytes = uploadPolicy.limitsBytes[category];
      if (!isFileSizeWithinLimitBytes(file.size, maxBytes)) {
        toast.error(
          `${t("fileUpload.fileTooLarge")} (${formatUploadLimitMiB(maxBytes)})`,
        );
        return false;
      }
      return true;
    },
    [uploadPolicy, t],
  );

  /** Validate file count (existing + new), returns true if ok */
  const validateCount = useCallback(
    (newFileCount: number): boolean => {
      if (!uploadPolicy) return true;
      const remaining = uploadPolicy.maxFiles - attachments.length;
      if (remaining <= 0 || newFileCount > remaining) {
        toast.error(
          t("fileUpload.tooManyFiles", { count: uploadPolicy.maxFiles }),
        );
        return false;
      }
      return true;
    },
    [uploadPolicy, attachments.length, t],
  );

  /** Cancel an in-progress upload by attachment id */
  const cancelUpload = useCallback(
    (id: string) => {
      uploadSourcesRef.current.delete(id);
      cancelTemporaryUpload(
        id,
        abortMapRef.current,
        cancelledUploadIdsRef.current,
        onAttachmentsChange,
      );
    },
    [onAttachmentsChange],
  );

  /** Cancel in-flight uploads and queue completed unbound files for deletion. */
  const clearUploads = useCallback(() => {
    clearAttachmentResources(attachments, cancelUpload);
    uploadSourcesRef.current.clear();
    onAttachmentsChange([]);
  }, [attachments, cancelUpload, onAttachmentsChange]);

  const removeAttachment = useCallback(
    (attachment: MessageAttachment) => {
      uploadSourcesRef.current.delete(attachment.id);
      removeAttachmentFromUpload(
        attachment,
        cancelUpload,
        onAttachmentsChange,
      );
    },
    [cancelUpload, onAttachmentsChange],
  );

  /** Upload a single file with progress tracking */
  const uploadFile = useCallback(
    (file: File, category?: FileCategory) => {
      if (isDuplicateFileUpload(file, uploadSourcesRef.current)) {
        toast.error(String(t("fileUpload.duplicateFile")));
        return;
      }

      const fileCategory = category || getFileCategory(file);
      const reservation = uploadSchedulerRef.current.reserve();
      const task = startFileUploadTask({
        file,
        fileCategory,
        t,
        onAttachmentsChange,
        abortMap: abortMapRef.current,
        cancelled: cancelledUploadIdsRef.current,
        waitFor: reservation.waitFor,
        prepareFile: (source) =>
          fileCategory === "image"
            ? compressImageFile(source).catch(() => source)
            : Promise.resolve(source),
        uploadClient: uploadApi,
        createId: uuid,
        notifyError: toast.error,
        reportFailure: (error) => {
          console.error("[Upload] failed", {
            kind: error instanceof UploadRequestError ? error.kind : "recoverable",
            status: error instanceof UploadRequestError ? error.status : undefined,
          });
        },
        onAttachmentReplaced: (temporaryId, finalId) => {
          const source = uploadSourcesRef.current.get(temporaryId);
          if (!source) return;
          uploadSourcesRef.current.delete(temporaryId);
          uploadSourcesRef.current.set(finalId, source);
        },
        onTaskSettled: (temporaryId) => {
          uploadSourcesRef.current.delete(temporaryId);
          reservation.release();
        },
      });
      uploadSourcesRef.current.set(task.tempId, file);
    },
    [onAttachmentsChange, t],
  );

  /** Validate and upload multiple files */
  const uploadFiles = useCallback(
    (files: FileList | File[], category?: FileCategory) => {
      const fileArray = Array.from(files);
      if (fileArray.length === 0) return;

      const { accepted: acceptedFiles, rejected } = partitionAcceptedProfileFiles(
        fileArray,
        acceptedFileTypes,
      );
      if (rejected.length > 0) {
        toast.error(String(t("fileUpload.serverUnsupportedFileType")));
      }
      if (acceptedFiles.length === 0 || !validateCount(acceptedFiles.length)) return;

      for (const file of acceptedFiles) {
        const fileCategory = category || getFileCategory(file);
        if (!validateSize(file, fileCategory)) continue;
        uploadFile(file, fileCategory);
      }
    },
    [acceptedFileTypes, t, validateCount, validateSize, uploadFile],
  );

  const controls: FileUploadControls = {
    uploadLimitsBytes: uploadPolicy?.limitsBytes ?? null,
    uploadFiles,
    uploadFile,
    validateSize,
    validateCount,
    cancelUpload,
    clearUploads,
    removeAttachment,
  };

  return sharedControls ?? controls;
}

export { getFileCategory };
