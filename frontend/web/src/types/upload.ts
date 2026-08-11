// ============================================
// File Upload Types
// ============================================

export type FileCategory = "image" | "video" | "audio" | "document";

export interface MessageAttachment {
  id: string;
  key: string;
  name: string;
  type: FileCategory;
  mimeType: string;
  size: number;
  url?: string;
  /** Independently authorized download URL when it differs from preview. */
  downloadUrl?: string;
  /** Upload progress (0-100) */
  uploadProgress?: number;
  /** Whether upload is in progress */
  isUploading?: boolean;
}

export interface UploadLimitsBytes {
  image: number;
  video: number;
  audio: number;
  document: number;
}

export interface LegacyUploadLimits extends UploadLimitsBytes {
  maxFiles: number;
}

export interface UploadConfig {
  enabled: boolean;
  provider?: string;
  /** Canonical per-category limits. Every value is a byte count. */
  uploadLimitsBytes?: UploadLimitsBytes;
  maxFiles?: number;
  /** Pre-existing wire alias; category values remain byte counts. */
  uploadLimits?: LegacyUploadLimits;
  max_file_size_bytes?: number;
  max_file_size?: number;
}

export interface UploadResult {
  key: string;
  url: string;
  name: string;
  type: FileCategory;
  mimeType: string;
  size: number;
}
