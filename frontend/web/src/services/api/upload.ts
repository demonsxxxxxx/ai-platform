/**
 * Upload API - 文件上传
 */

import type { FileCategory, UploadConfig, UploadResult } from "../../types";
import { API_BASE } from "./config";
import { authFetch } from "./fetch";
import { authenticatedRequest } from "./authenticatedRequest";
import {
  getValidAccessToken,
  redirectToLogin,
  refreshAccessToken,
} from "./tokenManager";
import { getRefreshToken } from "./token";

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

function knownUploadErrorCode(
  detail: unknown,
): SafeUploadErrorCode | undefined {
  const candidate =
    typeof detail === "string"
      ? detail
      : detail !== null &&
          typeof detail === "object" &&
          !Array.isArray(detail) &&
          Object.prototype.hasOwnProperty.call(detail, "code")
        ? (detail as { code?: unknown }).code
        : undefined;
  if (candidate === "file_too_large" || candidate === "unsupported_file_type") {
    return candidate;
  }
  return undefined;
}

function uploadRequestErrorFromResponse(
  status: number,
  detail: unknown,
): UploadRequestError {
  const code = knownUploadErrorCode(detail);
  if (status === 413 && code === "file_too_large") {
    return new UploadRequestError("file_too_large", status, code);
  }
  if (status === 415 && code === "unsupported_file_type") {
    return new UploadRequestError("unsupported_file_type", status, code);
  }
  return new UploadRequestError("recoverable", status);
}

const MULTIPART_THRESHOLD_BYTES = 32 * 1024 * 1024;

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
    const completed = await authFetch<{
      file_id: string;
      name: string;
      sha256: string;
      size_bytes: number;
    }>(`${API_BASE}/api/ai/files/uploads/${start.upload_session_id}/complete`, {
      method: "POST",
      body: JSON.stringify({
        parts: completedParts.sort((left, right) => left.part_number - right.part_number),
      }),
      signal: controller.signal,
    });
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
    throw error;
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

    if (file.size > MULTIPART_THRESHOLD_BYTES) {
      return uploadMultipartFile(file, options);
    }

    const folder = options.folder || "uploads";
    const { onProgress } = options;

    let xhr = new XMLHttpRequest();
    let aborted = false;

    const promise = new Promise<UploadResult>((resolve, reject) => {
      const uploadOnce = async (retried: boolean) => {
        const formData = new FormData();
        formData.append("file", file);

        const token = await getValidAccessToken();
        if (aborted) {
          reject(new UploadRequestError("cancelled"));
          return;
        }

        xhr = new XMLHttpRequest();

        if (onProgress) {
          xhr.upload.addEventListener("progress", (event) => {
            if (aborted) {
              return;
            }
            if (event.lengthComputable) {
              const progress = Math.round((event.loaded / event.total) * 100);
              onProgress(progress, event.loaded, event.total);
            }
          });
        }

        xhr.addEventListener("load", async () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              const raw = JSON.parse(xhr.responseText);
              const result: UploadResult = {
                key: raw.key,
                url: raw.url,
                name: raw.name,
                type: raw.type,
                mimeType: raw.mimeType ?? raw.mime_type ?? "",
                size: raw.size,
              };
              resolve(result);
            } catch {
              reject(new Error("Failed to parse upload response"));
            }
            return;
          }

          if (xhr.status === 401 && !retried && getRefreshToken()) {
            try {
              await refreshAccessToken();
              await uploadOnce(true);
              return;
            } catch {
              redirectToLogin();
            }
          }

          try {
            const errorData = JSON.parse(xhr.responseText);
            reject(uploadRequestErrorFromResponse(xhr.status, errorData.detail));
          } catch {
            reject(uploadRequestErrorFromResponse(xhr.status, undefined));
          }
        });

        xhr.addEventListener("error", () => {
          reject(new UploadRequestError("recoverable"));
        });

        xhr.addEventListener("abort", () => {
          aborted = true;
          reject(new UploadRequestError("cancelled"));
        });

        const url = `${API_BASE}/api/upload/file?folder=${encodeURIComponent(
          folder,
        )}`;
        xhr.open("POST", url);
        xhr.withCredentials = true;

        if (token) {
          xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        }

        xhr.send(formData);
      };

      void uploadOnce(false);
    });

    return {
      promise,
      abort: () => {
        aborted = true;
        xhr.abort();
      },
    };
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
