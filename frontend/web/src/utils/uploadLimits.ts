import type { UploadConfig, UploadLimitsBytes } from "../types";

const MEBIBYTE_BYTES = 1024 * 1024;

export interface ResolvedUploadBytePolicy {
  limitsBytes: UploadLimitsBytes;
  maxFiles: number;
}

/** Resolve the explicit byte contract, with the byte-valued legacy alias as fallback. */
export function resolveUploadBytePolicy(
  config: UploadConfig,
): ResolvedUploadBytePolicy | null {
  const limitsBytes = config.uploadLimitsBytes ?? config.uploadLimits;
  const maxFiles = config.maxFiles ?? config.uploadLimits?.maxFiles;
  if (!limitsBytes || maxFiles === undefined) {
    return null;
  }
  return {
    limitsBytes: {
      image: limitsBytes.image,
      video: limitsBytes.video,
      audio: limitsBytes.audio,
      document: limitsBytes.document,
    },
    maxFiles,
  };
}

export function isFileSizeWithinLimitBytes(
  fileSizeBytes: number,
  limitBytes: number,
): boolean {
  return fileSizeBytes <= limitBytes;
}

export function formatUploadLimitMiB(limitBytes: number): string {
  const mebibytes = limitBytes / MEBIBYTE_BYTES;
  const formatted = Number.isInteger(mebibytes)
    ? String(mebibytes)
    : mebibytes.toFixed(1);
  return `${formatted} MiB`;
}
