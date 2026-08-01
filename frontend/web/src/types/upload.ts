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

export interface UploadConfig {
  enabled: boolean;
  provider?: string;
  uploadLimits: {
    image: number;
    video: number;
    audio: number;
    document: number;
    maxFiles: number;
  };
}

export interface UploadResult {
  key: string;
  url: string;
  name: string;
  type: FileCategory;
  mimeType: string;
  size: number;
}
