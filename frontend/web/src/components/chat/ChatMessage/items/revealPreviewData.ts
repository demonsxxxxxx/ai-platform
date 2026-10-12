import { shouldUseAuthenticatedDocumentRequest } from "../../../documents/documentFetchCache";
import {
  isAllowedAuthenticatedArtifactFileUrl,
  isSensitiveInternalPath,
} from "../../../documents/documentUrlSafety";

export { isSensitiveInternalPath };

export type RevealPreviewRequest = {
  kind: "file";
  previewKey: string;
  filePath: string;
  content?: string;
  s3Key?: string;
  signedUrl?: string;
  previewUrl?: string;
  downloadUrl?: string;
  imageUrl?: string;
  fileSize?: number;
  mimeType?: string;
};

export function sanitizeProjectPath(value: unknown): string {
  if (typeof value !== "string") return "";
  return isSensitiveInternalPath(value) ? "" : value;
}

export function isAllowedRevealArtifactUrl(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed || isSensitiveInternalPath(trimmed)) return false;
  if (!shouldUseAuthenticatedDocumentRequest(trimmed)) return false;
  return isAllowedAuthenticatedArtifactFileUrl(trimmed);
}

/** Historical tool-result labels used only to locate a message, never to load files. */
export function parseProjectRevealSummary(input: {
  args: Record<string, unknown>;
  result?: string | Record<string, unknown>;
}): { projectName: string; projectPath: string } {
  if (!input.result) {
    return {
      projectName: typeof input.args.name === "string" ? input.args.name : "",
      projectPath: "",
    };
  }
  try {
    const raw: unknown = typeof input.result === "string"
      ? JSON.parse(input.result)
      : input.result;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      return { projectName: "", projectPath: "" };
    }
    const result = raw as Record<string, unknown>;
    if (result.error) return { projectName: "", projectPath: "" };
    return {
      projectName: typeof result.name === "string" ? result.name : "",
      projectPath: sanitizeProjectPath(result.path),
    };
  } catch {
    return { projectName: "", projectPath: "" };
  }
}
