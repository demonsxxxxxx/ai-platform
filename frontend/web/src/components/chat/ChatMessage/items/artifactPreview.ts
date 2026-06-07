import {
  isAllowedRevealArtifactUrl,
  type RevealPreviewRequest,
} from "./revealPreviewData";
import { MIME_TO_EXT } from "../../../documents/utils";

export interface ArtifactPreviewInput {
  artifact_id?: string | null;
  id?: string | null;
  label?: string | null;
  content_type?: string | null;
  contentType?: string | null;
  size_bytes?: number | null;
  sizeBytes?: number | null;
  download_url?: string | null;
  downloadUrl?: string | null;
  preview_url?: string | null;
  previewUrl?: string | null;
}

function normalizeString(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

function normalizeArtifactUrl(value: string | null | undefined): string | null {
  const url = normalizeString(value);
  return url && isAllowedRevealArtifactUrl(url) ? url : null;
}

function normalizeSize(value: number | null | undefined): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : undefined;
}

function sanitizeFileLabel(label: string | null): string {
  const fallback = "artifact";
  if (!label) return fallback;
  const withoutQuery = label.split(/[?#]/)[0] || label;
  const basename = withoutQuery.split(/[\\/]/).filter(Boolean).pop();
  return basename?.trim() || fallback;
}

function ensureFileExtension(label: string, mimeType: string | null): string {
  const basename = label.split(/[\\/]/).pop() || label;
  const dotIndex = basename.lastIndexOf(".");
  if (dotIndex > 0 && dotIndex < basename.length - 1) {
    return label;
  }

  const mimeExtension = mimeType
    ? MIME_TO_EXT[mimeType.toLowerCase()]
    : undefined;
  return mimeExtension ? `${label}.${mimeExtension}` : label;
}

function sanitizePreviewKeyComponent(value: string): string {
  const sanitized = value
    .trim()
    .replace(/[^a-zA-Z0-9._:-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return sanitized || hashPreviewIdentity(value);
}

function hashPreviewIdentity(value: string): string {
  let hash = 5381;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 33) ^ value.charCodeAt(index);
  }
  return (hash >>> 0).toString(36);
}

function buildPreviewKey(input: {
  artifactId: string | null;
  filePath: string;
  url: string;
}): string {
  if (input.artifactId) {
    return `artifact:${sanitizePreviewKeyComponent(input.artifactId)}`;
  }
  return `artifact:${hashPreviewIdentity(`${input.filePath}|${input.url}`)}`;
}

export function buildArtifactPreviewRequest<T extends ArtifactPreviewInput>(
  artifact: T,
): Extract<RevealPreviewRequest, { kind: "file" }> | null {
  const previewUrl = normalizeArtifactUrl(
    artifact.preview_url ?? artifact.previewUrl,
  );
  const downloadUrl = normalizeArtifactUrl(
    artifact.download_url ?? artifact.downloadUrl,
  );
  const url = previewUrl ?? downloadUrl;
  if (!url) {
    return null;
  }

  const mimeType = normalizeString(artifact.content_type ?? artifact.contentType);
  const filePath = ensureFileExtension(
    sanitizeFileLabel(normalizeString(artifact.label)),
    mimeType,
  );
  const artifactId = normalizeString(artifact.artifact_id ?? artifact.id);
  const fileSize = normalizeSize(artifact.size_bytes ?? artifact.sizeBytes);

  return {
    kind: "file",
    previewKey: buildPreviewKey({ artifactId, filePath, url }),
    filePath,
    signedUrl: url,
    ...(fileSize !== undefined ? { fileSize } : {}),
    ...(mimeType ? { mimeType } : {}),
  };
}
