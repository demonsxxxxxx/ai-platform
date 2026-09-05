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
import type { MessageAttachment, FileCategory } from "../types";

export interface UseFileUploadOptions {
  attachments: MessageAttachment[];
  onAttachmentsChange: (
    attachments:
      | MessageAttachment[]
      | ((prev: MessageAttachment[]) => MessageAttachment[]),
  ) => void;
  acceptedFileTypes?: readonly string[];
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

interface FileUploadTaskOptions {
  file: File;
  fileCategory: FileCategory;
  t: UploadTranslation;
  onAttachmentsChange: UseFileUploadOptions["onAttachmentsChange"];
  abortMap: Map<string, () => void>;
  cancelled: Set<string>;
  prepareFile: (file: File) => Promise<File>;
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
}: UseFileUploadOptions) {
  const { t } = useTranslation();
  const [uploadPolicy, setUploadPolicy] =
    useState<ResolvedUploadBytePolicy | null>(null);
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
        if (isMounted) {
          setUploadPolicy(resolveUploadBytePolicy(config));
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
    onAttachmentsChange([]);
  }, [attachments, cancelUpload, onAttachmentsChange]);

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

  return {
    uploadLimitsBytes: uploadPolicy?.limitsBytes ?? null,
    uploadFiles,
    uploadFile,
    validateSize,
    validateCount,
    cancelUpload,
    clearUploads,
  };
}

export { getFileCategory };
