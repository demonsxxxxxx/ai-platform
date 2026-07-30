import { getFullUrl } from "../../../services/api/config";
import {
  buildSanitizedProjectRevealDataFromMeta,
  isAllowedRevealArtifactUrl,
  sanitizeProjectPath,
  type ProjectRevealMetaInput,
  type RevealPreviewRequest,
} from "../../chat/ChatMessage/items/revealPreviewData";

type RevealedArtifactSource = "reveal_file" | "reveal_project";

/** Safe artifact fields retained by Chat previews after the Files workbench removal. */
interface RevealedArtifactNavigationProjection {
  id: string;
  file_key: string;
  file_name: string;
  file_size: number;
  preview_url: string | null;
  download_url: string | null;
  url?: string | null;
  source: RevealedArtifactSource;
  original_path: string | null;
  project_meta?: ProjectRevealMetaInput | null;
  trace_id?: string | null;
  mime_type?: string | null;
}

export interface ExternalNavigationTargetFile {
  fileId?: string;
  fileKey?: string | null;
  fileName?: string;
  originalPath?: string | null;
  traceId?: string | null;
  source?: RevealedArtifactSource;
}

export interface ExternalNavigationState {
  externalNavigate?: boolean;
  scrollToBottom?: boolean;
  targetFile?: ExternalNavigationTargetFile | null;
}

type RevealedFileNavigationProjection = RevealedArtifactNavigationProjection;

export function shouldResetExternalNavigateFlag(
  locationState: ExternalNavigationState | null | undefined,
): boolean {
  return locationState?.externalNavigate === true;
}

export function shouldScrollToBottomAfterExternalNavigation(
  locationState: ExternalNavigationState | null | undefined,
): boolean {
  return (
    locationState?.externalNavigate === true &&
    locationState?.scrollToBottom === true
  );
}

export function getExternalNavigationTargetFile(
  locationState: ExternalNavigationState | null | undefined,
): ExternalNavigationTargetFile | null {
  if (locationState?.externalNavigate !== true) {
    return null;
  }

  const targetFile = locationState.targetFile;
  if (!targetFile) {
    return null;
  }

  const sanitizedTargetFile: ExternalNavigationTargetFile = {
    ...(targetFile.fileId ? { fileId: targetFile.fileId } : {}),
    ...(targetFile.traceId?.trim() ? { traceId: targetFile.traceId } : {}),
    ...(targetFile.source ? { source: targetFile.source } : {}),
  };

  const hasMatchableField =
    !!sanitizedTargetFile.fileId || !!sanitizedTargetFile.traceId?.trim();

  return hasMatchableField ? sanitizedTargetFile : null;
}

function getAllowedExternalFileSignedUrl(
  url: string | null | undefined,
): string | undefined {
  const trimmed = url?.trim();
  if (!trimmed || !isAllowedRevealArtifactUrl(trimmed)) {
    return undefined;
  }
  return getFullUrl(trimmed);
}

function sanitizeExternalFilePathValue(
  value: string | null | undefined,
): string {
  const trimmed = value?.trim();
  return trimmed ? sanitizeProjectPath(trimmed).trim() : "";
}

function getSafeExternalFilePath(file: {
  original_path?: string | null;
  file_name: string;
}): string {
  return (
    sanitizeExternalFilePathValue(file.original_path) ||
    sanitizeExternalFilePathValue(file.file_name)
  );
}

function getSafeExternalFileS3Key(
  fileKey: string | null | undefined,
): string | undefined {
  return sanitizeExternalFilePathValue(fileKey) || undefined;
}

function buildExternalNavigationTargetFile(file: {
  id: string;
  trace_id?: string | null;
  source?: RevealedArtifactSource;
}): ExternalNavigationTargetFile {
  return {
    fileId: file.id,
    source: file.source,
    ...(file.trace_id ? { traceId: file.trace_id } : {}),
  };
}

export function buildExternalNavigationPreviewRequest(
  file: RevealedFileNavigationProjection,
): RevealPreviewRequest | null {
  if (file.source === "reveal_project") {
    const project = buildSanitizedProjectRevealDataFromMeta({
      name: file.file_name,
      path: file.original_path,
      meta: file.project_meta,
    });
    if (!project) {
      return null;
    }

    return {
      kind: "project",
      previewKey: `external-project:${file.id}`,
      project,
    };
  }

  const filePath = getSafeExternalFilePath(file);
  const s3Key = getSafeExternalFileS3Key(file.file_key);
  const previewUrl = getAllowedExternalFileSignedUrl(file.preview_url);
  const downloadUrl = getAllowedExternalFileSignedUrl(file.download_url);

  return {
    kind: "file",
    previewKey: `external-file:${file.id}`,
    filePath,
    ...(s3Key ? { s3Key } : {}),
    ...(previewUrl ? { signedUrl: previewUrl } : {}),
    ...(previewUrl ? { previewUrl } : {}),
    ...(downloadUrl ? { downloadUrl } : {}),
    fileSize: file.file_size,
    ...(file.mime_type ? { mimeType: file.mime_type } : {}),
  };
}

export function buildExternalNavigationStateForFile(
  file: Pick<
    RevealedArtifactNavigationProjection,
    | "id"
    | "file_key"
    | "file_name"
    | "file_size"
    | "url"
    | "source"
    | "original_path"
    | "project_meta"
    | "trace_id"
  >,
): ExternalNavigationState {
  return {
    externalNavigate: true,
    targetFile: buildExternalNavigationTargetFile(file),
  };
}
