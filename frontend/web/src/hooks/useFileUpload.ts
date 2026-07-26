import { useState, useCallback, useRef, useEffect } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { uploadApi, UploadRequestError } from "../services/api/upload";
import type { FileCheckResult } from "../types";
import { compressImageFile } from "../utils/imageCompression";
import { uuid } from "../utils/uuid";
import type { MessageAttachment, FileCategory } from "../types";

export interface UploadLimits {
  image: number;
  video: number;
  audio: number;
  document: number;
  maxFiles: number;
}

export interface UseFileUploadOptions {
  attachments: MessageAttachment[];
  onAttachmentsChange: (
    attachments:
      | MessageAttachment[]
      | ((prev: MessageAttachment[]) => MessageAttachment[]),
  ) => void;
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

function computeFileHash(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(
      new URL("../workers/hashWorker.ts", import.meta.url),
      { type: "module" },
    );
    worker.onmessage = (e) => {
      worker.terminate();
      if (e.data.error) {
        reject(new Error(e.data.error));
      } else {
        resolve(e.data.hash);
      }
    };
    worker.onerror = (e) => {
      worker.terminate();
      reject(new Error(e.message));
    };
    worker.postMessage({ file });
  });
}

type UploadClient = Pick<typeof uploadApi, "checkFile"> & {
  uploadFile: (
    file: File,
    options: { onProgress?: (progress: number) => void },
  ) => ReturnType<typeof uploadApi.uploadFile>;
};

interface FileUploadTaskOptions {
  file: File;
  fileCategory: FileCategory;
  t: UploadTranslation;
  onAttachmentsChange: UseFileUploadOptions["onAttachmentsChange"];
  abortMap: Map<string, () => void>;
  cancelled: Set<string>;
  prepareFile: (file: File) => Promise<File>;
  hashFile: (file: File) => Promise<string>;
  uploadClient: UploadClient;
  createId: () => string;
  notifyError: (message: string) => void;
  reportFailure: (error: unknown) => void;
}

interface FileUploadTask {
  tempId: string;
  done: Promise<void>;
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

/** Owns the full lifecycle of one temporary upload attachment. */
export function startFileUploadTask({
  file,
  fileCategory,
  t,
  onAttachmentsChange,
  abortMap,
  cancelled,
  prepareFile,
  hashFile,
  uploadClient,
  createId,
  notifyError,
  reportFailure,
}: FileUploadTaskOptions): FileUploadTask {
  const tempId = `temp-${createId()}`;
  const isCancelled = () => cancelled.has(tempId);
  const finish = () => {
    abortMap.delete(tempId);
    cancelled.delete(tempId);
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
    isUploading: true,
  };
  onAttachmentsChange((previous) => [...previous, tempAttachment]);

  const done = (async () => {
    try {
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

      let check: FileCheckResult = { exists: false };
      try {
        const hash = await hashFile(processedFile);
        if (isCancelled()) {
          finish();
          return;
        }
        onAttachmentsChange((previous) =>
          previous.map((attachment) =>
            attachment.id === tempId
              ? { ...attachment, uploadProgress: 1 }
              : attachment,
          ),
        );
        check = await uploadClient.checkFile(
          hash,
          processedFile.size,
          processedFile.name,
          processedFile.type,
        );
      } catch {
        if (isCancelled()) {
          finish();
          return;
        }
      }

      if (isCancelled()) {
        finish();
        return;
      }
      if (check.exists && "key" in check) {
        const existing = check as FileCheckResult;
        const finalAttachment: MessageAttachment = {
          id: createId(),
          key: existing.key ?? "",
          name: existing.name || processedFile.name,
          type: existing.type as FileCategory,
          mimeType: existing.mimeType ?? processedFile.type,
          size: existing.size ?? processedFile.size,
          url: existing.url || `/api/upload/file/${existing.key ?? ""}`,
        };
        onAttachmentsChange((previous) =>
          previous.map((attachment) =>
            attachment.id === tempId
              ? {
                  ...finalAttachment,
                  uploadProgress: 100,
                  isUploading: false,
                }
              : attachment,
          ),
        );
        finish();
        return;
      }

      const handle = uploadClient.uploadFile(processedFile, {
        onProgress: (progress) => {
          if (isCancelled()) {
            return;
          }
          onAttachmentsChange((previous) =>
            previous.map((attachment) =>
              attachment.id === tempId
                ? { ...attachment, uploadProgress: progress, isUploading: true }
                : attachment,
            ),
          );
        },
      });
      abortMap.set(tempId, handle.abort);
      const result = await handle.promise;
      if (isCancelled()) {
        finish();
        return;
      }
      const finalAttachment: MessageAttachment = {
        id: createId(),
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

export function useFileUpload({
  attachments,
  onAttachmentsChange,
}: UseFileUploadOptions) {
  const { t } = useTranslation();
  const [uploadLimits, setUploadLimits] = useState<UploadLimits | null>(null);
  const limitsFetched = useRef(false);
  const abortMapRef = useRef<Map<string, () => void>>(new Map());
  const cancelledUploadIdsRef = useRef<Set<string>>(new Set());

  // Fetch upload limits once
  useEffect(() => {
    if (limitsFetched.current) {
      return;
    }

    limitsFetched.current = true;
    let isMounted = true;

    uploadApi
      .getConfig()
      .then((config) => {
        if (isMounted && config.uploadLimits) {
          setUploadLimits(config.uploadLimits);
        }
      })
      .catch(() => {});

    return () => {
      isMounted = false;
    };
  }, []);

  /** Validate file size, returns true if ok */
  const validateSize = useCallback(
    (file: File, category: FileCategory): boolean => {
      if (!uploadLimits) return true;
      const maxMB = uploadLimits[category];
      if (file.size > maxMB * 1024 * 1024) {
        toast.error(`${t("fileUpload.fileTooLarge")} (${maxMB}MB)`);
        return false;
      }
      return true;
    },
    [uploadLimits, t],
  );

  /** Validate file count (existing + new), returns true if ok */
  const validateCount = useCallback(
    (newFileCount: number): boolean => {
      if (!uploadLimits) return true;
      const remaining = uploadLimits.maxFiles - attachments.length;
      if (remaining <= 0 || newFileCount > remaining) {
        toast.error(
          t("fileUpload.tooManyFiles", { count: uploadLimits.maxFiles }),
        );
        return false;
      }
      return true;
    },
    [uploadLimits, attachments.length, t],
  );

  /** Cancel an in-progress upload by attachment id */
  const cancelUpload = useCallback(
    (id: string) => {
      cancelTemporaryUpload(
        id,
        abortMapRef.current,
        cancelledUploadIdsRef.current,
        onAttachmentsChange,
      );
    },
    [onAttachmentsChange],
  );

  /** Upload a single file with progress tracking */
  const uploadFile = useCallback(
    (file: File, category?: FileCategory) => {
      const fileCategory = category || getFileCategory(file);
      startFileUploadTask({
        file,
        fileCategory,
        t,
        onAttachmentsChange,
        abortMap: abortMapRef.current,
        cancelled: cancelledUploadIdsRef.current,
        prepareFile: (source) =>
          fileCategory === "image"
            ? compressImageFile(source).catch(() => source)
            : Promise.resolve(source),
        hashFile: computeFileHash,
        uploadClient: uploadApi,
        createId: uuid,
        notifyError: toast.error,
        reportFailure: (error) => {
          console.error("[Upload] failed", {
            kind: error instanceof UploadRequestError ? error.kind : "recoverable",
            status: error instanceof UploadRequestError ? error.status : undefined,
          });
        },
      });
    },
    [onAttachmentsChange, t],
  );

  /** Validate and upload multiple files */
  const uploadFiles = useCallback(
    (files: FileList | File[], category?: FileCategory) => {
      const fileArray = Array.from(files);
      if (fileArray.length === 0) return;

      if (!validateCount(fileArray.length)) return;

      for (const file of fileArray) {
        const fileCategory = category || getFileCategory(file);
        if (!validateSize(file, fileCategory)) continue;
        uploadFile(file, fileCategory);
      }
    },
    [validateCount, validateSize, uploadFile],
  );

  return {
    uploadLimits,
    uploadFiles,
    uploadFile,
    validateSize,
    validateCount,
    cancelUpload,
  };
}

export { getFileCategory };
