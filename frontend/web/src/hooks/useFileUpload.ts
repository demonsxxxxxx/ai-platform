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

export function useFileUpload({
  attachments,
  onAttachmentsChange,
}: UseFileUploadOptions) {
  const { t } = useTranslation();
  const [uploadLimits, setUploadLimits] = useState<UploadLimits | null>(null);
  const limitsFetched = useRef(false);
  const abortMapRef = useRef<Map<string, () => void>>(new Map());

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
      const abort = abortMapRef.current.get(id);
      if (abort) {
        abort();
        abortMapRef.current.delete(id);
      }
      onAttachmentsChange((prev) => prev.filter((a) => a.id !== id));
    },
    [onAttachmentsChange],
  );

  /** Upload a single file with progress tracking */
  const uploadFile = useCallback(
    (file: File, category?: FileCategory) => {
      const fileCategory = category || getFileCategory(file);

      // Compress images before upload
      const maybeCompress =
        fileCategory === "image"
          ? compressImageFile(file).catch(() => file)
          : Promise.resolve(file);

      maybeCompress.then((processedFile) => {
        const tempId = `temp-${uuid()}`;

        const tempAttachment: MessageAttachment = {
          id: tempId,
          key: "",
          name: processedFile.name,
          type: fileCategory,
          mimeType: processedFile.type,
          size: processedFile.size,
          url: "",
          uploadProgress: 0,
          isUploading: true,
        };

        onAttachmentsChange((prev) => [...prev, tempAttachment]);

        computeFileHash(processedFile)
          .then((hash) => {
            onAttachmentsChange((prev: MessageAttachment[]) =>
              prev.map((a) =>
                a.id === tempId ? { ...a, uploadProgress: 1 } : a,
              ),
            );
            return uploadApi
              .checkFile(
                hash,
                processedFile.size,
                processedFile.name,
                processedFile.type,
              )
              .then((check) => ({ check }));
          })
          .catch(() => ({ check: { exists: false } }))
          .then(({ check }) => {
            if (check.exists && 'key' in check) {
              abortMapRef.current.delete(tempId);
              const c = check as FileCheckResult;
              const finalAttachment: MessageAttachment = {
                id: uuid(),
                key: c.key ?? "",
                name: c.name || processedFile.name,
                type: c.type as FileCategory,
                mimeType: c.mimeType ?? processedFile.type,
                size: c.size ?? processedFile.size,
                url: c.url || `/api/upload/file/${c.key ?? ""}`,
              };
              onAttachmentsChange((prev: MessageAttachment[]) =>
                prev.map((a) =>
                  a.id === tempId
                    ? {
                        ...finalAttachment,
                        uploadProgress: 100,
                        isUploading: false,
                      }
                    : a,
                ),
              );
              return;
            }

            const handle = uploadApi.uploadFile(processedFile, {
              onProgress: (progress) => {
                onAttachmentsChange((prev: MessageAttachment[]) =>
                  prev.map((a) =>
                    a.id === tempId
                      ? { ...a, uploadProgress: progress, isUploading: true }
                      : a,
                  ),
                );
              },
            });

            abortMapRef.current.set(tempId, handle.abort);

            return handle.promise.then((result) => {
              abortMapRef.current.delete(tempId);
              const finalAttachment: MessageAttachment = {
                id: uuid(),
                key: result.key,
                name: result.name || processedFile.name,
                type: result.type as FileCategory,
                mimeType: result.mimeType,
                size: result.size,
                url: result.url,
              };
              onAttachmentsChange((prev: MessageAttachment[]) =>
                prev.map((a) => (a.id === tempId ? finalAttachment : a)),
              );
            });
          })
          .catch((error) => {
            abortMapRef.current.delete(tempId);
            const message = settleUploadFailure(error, t, () => {
              onAttachmentsChange((prev: MessageAttachment[]) =>
                prev.filter((attachment) => attachment.id !== tempId),
              );
            });
            if (message === null) {
              return;
            }
            console.error("[Upload] failed", {
              kind: error instanceof UploadRequestError ? error.kind : "recoverable",
              status: error instanceof UploadRequestError ? error.status : undefined,
            });
            toast.error(message);
          });
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
