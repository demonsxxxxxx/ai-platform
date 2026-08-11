import type { UploadConfig, UploadLimitsBytes } from "../types";

const MEBIBYTE_BYTES = 1024 * 1024;

export interface ResolvedUploadBytePolicy {
  limitsBytes: UploadLimitsBytes;
  maxFiles: number;
}

function nonNegativeSafeInteger(value: unknown): number | null {
  return typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0
    ? value
    : null;
}

function resolveCategoryLimitBytes(
  config: UploadConfig,
  category: keyof UploadLimitsBytes,
): number | null {
  return (
    nonNegativeSafeInteger(config.uploadLimitsBytes?.[category]) ??
    nonNegativeSafeInteger(config.uploadLimits?.[category])
  );
}

/** Resolve the explicit byte contract, with the byte-valued legacy alias as fallback. */
export function resolveUploadBytePolicy(
  config: UploadConfig | null | undefined,
): ResolvedUploadBytePolicy | null {
  if (!config || typeof config !== "object") {
    return null;
  }
  const image = resolveCategoryLimitBytes(config, "image");
  const video = resolveCategoryLimitBytes(config, "video");
  const audio = resolveCategoryLimitBytes(config, "audio");
  const document = resolveCategoryLimitBytes(config, "document");
  const maxFiles =
    nonNegativeSafeInteger(config.maxFiles) ??
    nonNegativeSafeInteger(config.uploadLimits?.maxFiles);
  if (
    image === null ||
    video === null ||
    audio === null ||
    document === null ||
    maxFiles === null
  ) {
    return null;
  }
  return {
    limitsBytes: {
      image,
      video,
      audio,
      document,
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
