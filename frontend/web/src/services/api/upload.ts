/**
 * Upload API - 文件上传
 */

import type { FileCategory, UploadConfig, UploadResult } from "../../types";
import { API_BASE } from "./config";
import { ApiRequestError, authFetch } from "./fetch";
import { authenticatedRequest } from "./authenticatedRequest";

interface SignedUrlItem {
  key: string;
  url: string | null;
  error?: string;
}

export interface UploadOptions {
  folder?: string;
  onProgress?: (progress: number, loaded: number, total: number) => void;
}

export interface UploadHandle {
  promise: Promise<UploadResult>;
  abort: () => void;
}

export type UploadRequestErrorKind =
  | "file_too_large"
  | "unsupported_file_type"
  | "recoverable"
  | "cancelled";

type SafeUploadErrorCode = "file_too_large" | "unsupported_file_type";

/** A bounded upload failure projection that never contains backend detail. */
export class UploadRequestError extends Error {
  constructor(
    readonly kind: UploadRequestErrorKind,
    readonly status?: number,
    readonly code?: SafeUploadErrorCode,
  ) {
    super("Upload request failed");
    this.name = "UploadRequestError";
  }
}

interface MultipartUploadResponse {
  upload_session_id: string;
  part_size_bytes: number;
  parts: Array<{ part_number: number; url: string }>;
}

function fileCategory(file: File): FileCategory {
  if (file.type.startsWith("image/")) return "image";
  if (file.type.startsWith("video/")) return "video";
  if (file.type.startsWith("audio/")) return "audio";
  return "document";
}

function uploadResultFromMultipart(
  raw: { file_id: string; name: string; sha256: string; size_bytes: number },
  file: File,
): UploadResult {
  return {
    key: raw.file_id,
    url: `/api/ai/files/${raw.file_id}`,
    name: raw.name,
    type: fileCategory(file),
    mimeType: file.type || "application/octet-stream",
    size: raw.size_bytes,
  };
}

function uploadMultipartFile(file: File, options: UploadOptions): UploadHandle {
  const controller = new AbortController();
  let aborted = false;
  let uploadSessionId: string | null = null;
  const promise = (async () => {
    const start = await authFetch<MultipartUploadResponse>(
      `${API_BASE}/api/ai/files/uploads`,
      {
        method: "POST",
        body: JSON.stringify({
          name: file.name,
          content_type: file.type || "application/octet-stream",
          size_bytes: file.size,
        }),
        signal: controller.signal,
      },
    );
    uploadSessionId = start.upload_session_id;
    const completedParts: Array<{ part_number: number; etag: string }> = [];
    let nextPart = 0;
    let loaded = 0;
    const uploadPart = async () => {
      while (nextPart < start.parts.length) {
        const part = start.parts[nextPart++];
        const startByte = (part.part_number - 1) * start.part_size_bytes;
        const endByte = Math.min(startByte + start.part_size_bytes, file.size);
        let response: Response | undefined;
        let etag: string | null = null;
        for (let attempt = 0; attempt <= 3; attempt += 1) {
          try {
            response = await fetch(part.url, {
              method: "PUT",
              body: file.slice(startByte, endByte),
              credentials: "include",
              headers: {
                "Content-Type": "application/octet-stream",
                "Accept-Language": "zh-CN",
              },
              signal: controller.signal,
            });
            if (!response.ok && response.status >= 400 && response.status < 500 && ![408, 429].includes(response.status)) {
              throw new UploadRequestError("recoverable", response.status);
            }
            const payload = (await response.json().catch(() => null)) as {
              etag?: unknown;
            } | null;
            etag = typeof payload?.etag === "string" ? payload.etag : null;
            if (response.ok && etag) break;
            throw new UploadRequestError("recoverable", response.status);
          } catch (error) {
            if (
              response &&
              !response.ok &&
              response.status >= 400 &&
              response.status < 500 &&
              ![408, 429].includes(response.status)
            ) {
              throw error;
            }
            if (attempt === 3 || controller.signal.aborted) throw error;
            await new Promise((resolve) => setTimeout(resolve, 250 * 2 ** attempt));
          }
        }
        if (!response?.ok || !etag) {
          throw new UploadRequestError("recoverable", response?.status ?? 503);
        }
        completedParts.push({ part_number: part.part_number, etag });
        loaded += endByte - startByte;
        options.onProgress?.(Math.round((loaded / file.size) * 100), loaded, file.size);
      }
    };
    await Promise.all([uploadPart(), uploadPart(), uploadPart()]);
    const completionBody = JSON.stringify({
      parts: completedParts.sort((left, right) => left.part_number - right.part_number),
    });
    let completed:
      | { file_id: string; name: string; sha256: string; size_bytes: number }
      | undefined;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        completed = await authFetch<{
          file_id: string;
          name: string;
          sha256: string;
          size_bytes: number;
        }>(`${API_BASE}/api/ai/files/uploads/${start.upload_session_id}/complete`, {
          method: "POST",
          body: completionBody,
          signal: controller.signal,
        });
        break;
      } catch (error) {
        if (attempt === 2 || controller.signal.aborted) throw error;
        await new Promise((resolve) => setTimeout(resolve, 250 * 2 ** attempt));
      }
    }
    if (!completed) throw new UploadRequestError("recoverable", 503);
    return uploadResultFromMultipart(completed, file);
  })().catch(async (error) => {
    if (uploadSessionId) {
      await authFetch(`${API_BASE}/api/ai/files/uploads/${uploadSessionId}/abort`, {
        method: "POST",
      }).catch(() => undefined);
    }
    if (aborted) {
      throw new UploadRequestError("cancelled");
    }
    if (
      error instanceof ApiRequestError &&
      (error.code === "file_too_large" || error.code === "unsupported_file_type")
    ) {
      throw new UploadRequestError(error.code, error.status, error.code);
    }
    if (error instanceof UploadRequestError) throw error;
    throw new UploadRequestError(
      "recoverable",
      error instanceof ApiRequestError ? error.status : undefined,
    );
  });
  return {
    promise,
    abort: () => {
      aborted = true;
      controller.abort();
    },
  };
}


let _configPromise: Promise<UploadConfig> | null = null;

export const uploadApi = {
  /**
   * 上传文件
   * @param file - The file to upload
   * @param folderOrOptions - Either a folder string (for backward compatibility) or UploadOptions object
   */
  uploadFile(
    file: File,
    folderOrOptions: string | UploadOptions = "uploads",
  ): UploadHandle {
    // Handle backward compatibility: string folder or options object
    const options: UploadOptions =
      typeof folderOrOptions === "string"
        ? { folder: folderOrOptions }
        : folderOrOptions;

    return uploadMultipartFile(file, options);
  },

  /**
   * 获取存储配置
   */
  async getConfig(): Promise<UploadConfig> {
    if (!_configPromise) {
      _configPromise = authFetch<UploadConfig>(`${API_BASE}/api/upload/config`);
    }
    return _configPromise;
  },

  /**
   * 获取 S3 签名 URL（用于访问私有文件）
   */
  async getSignedUrl(key: string, expires: number = 3600): Promise<string> {
    const result = await authFetch<SignedUrlItem>(
      `${API_BASE}/api/upload/signed-url?key=${encodeURIComponent(
        key,
      )}&expires=${expires}`,
    );
    if (result.error || !result.url) {
      throw new Error(result.error || "Failed to get signed URL");
    }
    return result.url;
  },

  /**
   * 删除上传的文件
   */
  async deleteFile(key: string): Promise<{ deleted: boolean; key: string }> {
    const response = await authenticatedRequest(
      `${API_BASE}/api/ai/files/${encodeURIComponent(key)}`,
      {
        method: "DELETE",
        credentials: "include",
      },
    );

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        errorData.detail || `Delete failed: ${response.statusText}`,
      );
    }

    return response.json();
  },
};
