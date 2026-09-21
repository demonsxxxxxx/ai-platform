import type {
  SessionInputFile,
  SessionInputFilesResponse,
} from "../../../services/api";
import type { ArtifactPart, Message, MessageAttachment, MessagePart } from "../../../types";

export type SessionWorkspaceFilesStatus =
  | "idle"
  | "loading"
  | "ready"
  | "partial"
  | "error";

export interface SessionWorkspaceFile {
  key: string;
  id: string;
  source: "input" | "artifact";
  name: string;
  mime_type: string;
  size_bytes: number;
  preview_url: string | null;
  download_url: string | null;
  created_at: string | null;
}

export interface SessionWorkspaceProjection {
  session_id: string | null;
  inputFiles: SessionInputFile[];
  files: SessionWorkspaceFile[];
  status: SessionWorkspaceFilesStatus;
}

export function sessionInputFileToWorkspaceFile(
  file: SessionInputFile,
): SessionWorkspaceFile {
  return {
    key: `input:${file.file_id}`,
    id: file.file_id,
    source: "input",
    name: file.name,
    mime_type: file.mime_type,
    size_bytes: file.size_bytes,
    preview_url: file.preview_url,
    download_url: file.download_url,
    created_at: file.created_at ?? null,
  };
}

function artifactWorkspaceFile(file: ArtifactPart): SessionWorkspaceFile {
  return {
    key: `artifact:${file.artifact_id}`,
    id: file.artifact_id,
    source: "artifact",
    name: file.label,
    mime_type: file.content_type || "application/octet-stream",
    size_bytes: file.size_bytes,
    preview_url: file.preview_url ?? null,
    download_url: file.download_url ?? null,
    created_at: file.created_at ?? null,
  };
}

function createdAtMillis(value: string | null): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function compareWorkspaceFiles(
  left: SessionWorkspaceFile,
  right: SessionWorkspaceFile,
): number {
  const createdDelta =
    createdAtMillis(right.created_at) - createdAtMillis(left.created_at);
  if (createdDelta !== 0) return createdDelta;
  if (left.name !== right.name) return left.name < right.name ? -1 : 1;
  return left.key < right.key ? -1 : left.key === right.key ? 0 : 1;
}

function exactInputFiles(
  sessionId: string,
  result: PromiseSettledResult<SessionInputFilesResponse>,
): SessionInputFile[] | null {
  return result.status === "fulfilled" && result.value.session_id === sessionId
    ? result.value.files
    : null;
}

/** Build the file-only panel projection from the authorized input source. */
export function projectSessionWorkspaceFiles(
  sessionId: string,
  inputResult: PromiseSettledResult<SessionInputFilesResponse>,
): SessionWorkspaceProjection {
  const inputFiles = exactInputFiles(sessionId, inputResult);
  return {
    session_id: sessionId,
    inputFiles: inputFiles ?? [],
    files: (inputFiles ?? [])
      .map(sessionInputFileToWorkspaceFile)
      .sort(compareWorkspaceFiles),
    status: inputFiles === null ? "error" : "ready",
  };
}

function collectArtifactParts(
  parts: readonly MessagePart[] | undefined,
  filesByKey: Map<string, SessionWorkspaceFile>,
): void {
  for (const part of parts ?? []) {
    if (part.type === "artifact") {
      const projected = artifactWorkspaceFile(part);
      filesByKey.set(projected.key, projected);
    } else if (part.type === "subagent") {
      collectArtifactParts(part.parts, filesByKey);
    }
  }
}

export function addSessionInputFile(
  projection: SessionWorkspaceProjection,
  file: SessionInputFile,
): SessionWorkspaceProjection {
  const workspaceFile = sessionInputFileToWorkspaceFile(file);
  return {
    ...projection,
    inputFiles: [
      ...projection.inputFiles.filter((item) => item.file_id !== file.file_id),
      file,
    ],
    files: [
      ...projection.files.filter((item) => item.key !== workspaceFile.key),
      workspaceFile,
    ].sort(compareWorkspaceFiles),
    status: projection.status === "idle" ? "ready" : projection.status,
  };
}

export function preservePendingSessionInputFiles(
  loaded: SessionWorkspaceProjection,
  current: SessionWorkspaceProjection,
): SessionWorkspaceProjection {
  if (loaded.session_id !== current.session_id) return loaded;
  const loadedIds = new Set(loaded.inputFiles.map((file) => file.file_id));
  let retainedPendingFile = false;
  const projection = current.inputFiles.reduce((next, file) => {
    if (file.run_id !== null || loadedIds.has(file.file_id)) return next;
    retainedPendingFile = true;
    return addSessionInputFile(next, file);
  }, loaded);
  return retainedPendingFile && loaded.status === "error"
    ? { ...projection, status: "partial" }
    : projection;
}

/** Add only structured files bound to assistant responses. */
export function projectAssistantResponseFiles(
  projection: SessionWorkspaceProjection,
  messages: readonly Message[],
): SessionWorkspaceProjection {
  const filesByKey = new Map(
    projection.files.map((file) => [file.key, file] as const),
  );
  for (const message of messages) {
    if (message.role === "assistant") {
      collectArtifactParts(message.parts, filesByKey);
    }
  }
  return {
    ...projection,
    files: [...filesByKey.values()].sort(compareWorkspaceFiles),
  };
}

/** Hide a projection synchronously when React starts rendering another session. */
export function sessionWorkspaceProjectionForRender(
  projection: SessionWorkspaceProjection,
  sessionId: string | null,
): SessionWorkspaceProjection {
  if (projection.session_id === sessionId) return projection;
  return {
    session_id: sessionId,
    inputFiles: [],
    files: [],
    status: sessionId ? "loading" : "idle",
  };
}

/** Convert one workspace file into the existing secure preview attachment shape. */
export function sessionWorkspaceFileToAttachment(
  file: SessionWorkspaceFile,
): MessageAttachment {
  const mimeType = file.mime_type.toLowerCase();
  const type: MessageAttachment["type"] = mimeType.startsWith("image/")
    ? "image"
    : mimeType.startsWith("video/")
      ? "video"
      : mimeType.startsWith("audio/")
        ? "audio"
        : "document";
  return {
    id: file.id,
    key: file.key,
    name: file.name,
    type,
    mimeType: file.mime_type,
    size: file.size_bytes,
    ...(file.preview_url || file.download_url
      ? { url: file.preview_url ?? file.download_url ?? undefined }
      : {}),
    ...(file.download_url ? { downloadUrl: file.download_url } : {}),
  };
}
