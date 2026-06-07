import { getFullUrl } from "../../../../services/api/config";
import {
  isAllowedRevealArtifactUrl,
  sanitizeProjectPath,
  type RevealPreviewRequest,
} from "./revealPreviewData";

function normalizeString(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

function hashPreviewIdentity(value: string): string {
  let hash = 5381;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 33) ^ value.charCodeAt(index);
  }
  return (hash >>> 0).toString(36);
}

export function buildFileLinkPreviewRequest(input: {
  href: string;
  fileName: string;
}): Extract<RevealPreviewRequest, { kind: "file" }> | null {
  const fullUrl = normalizeString(getFullUrl(input.href) || input.href);
  if (!fullUrl || !isAllowedRevealArtifactUrl(fullUrl)) {
    return null;
  }

  const filePath = sanitizeProjectPath(input.fileName).trim() || "file";

  return {
    kind: "file",
    previewKey: `file-link:${hashPreviewIdentity(`${filePath}|${fullUrl}`)}`,
    filePath,
    signedUrl: fullUrl,
  };
}
