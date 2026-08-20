import type {
  SessionArtifactFile,
  SessionArtifactFilesResponse,
  SessionInputFile,
  SessionInputFilesResponse,
} from "../../../services/api";
import type { MessageAttachment } from "../../../types";

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

function inputWorkspaceFile(file: SessionInputFile): SessionWorkspaceFile {
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

function artifactWorkspaceFile(file: SessionArtifactFile): SessionWorkspaceFile {
  return {
    key: `artifact:${file.id}`,
    id: file.id,
    source: "artifact",
    name: file.file_name,
    mime_type: file.mime_type ?? "application/octet-stream",
    size_bytes: file.file_size,
    preview_url: file.preview_url,
    download_url: file.download_url,
    created_at: file.created_at,
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

function exactArtifactFiles(
  sessionId: string,
  result: PromiseSettledResult<SessionArtifactFilesResponse>,
): SessionArtifactFile[] | null {
  return result.status === "fulfilled" && result.value.session_id === sessionId
    ? result.value.files
    : null;
}

/** Build the file-only panel projection from independently authorized sources. */
export function projectSessionWorkspaceFiles(
  sessionId: string,
  inputResult: PromiseSettledResult<SessionInputFilesResponse>,
  artifactResult: PromiseSettledResult<SessionArtifactFilesResponse>,
): SessionWorkspaceProjection {
  const inputFiles = exactInputFiles(sessionId, inputResult);
  const artifactFiles = exactArtifactFiles(sessionId, artifactResult);
  const sourcesReady = Number(inputFiles !== null) + Number(artifactFiles !== null);
  const filesByKey = new Map<string, SessionWorkspaceFile>();
  inputFiles?.forEach((file) => {
    const projected = inputWorkspaceFile(file);
    filesByKey.set(projected.key, projected);
  });
  artifactFiles?.forEach((file) => {
    const projected = artifactWorkspaceFile(file);
    filesByKey.set(projected.key, projected);
  });
  return {
    session_id: sessionId,
    inputFiles: inputFiles ?? [],
    files: [...filesByKey.values()].sort(compareWorkspaceFiles),
    status: sourcesReady === 2 ? "ready" : sourcesReady === 1 ? "partial" : "error",
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
