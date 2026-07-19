import { API_BASE } from "./config";
import { authenticatedRequest } from "./authenticatedRequest";

type AuthenticatedDownloadRequest = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

interface DownloadAuthenticatedFileOptions {
  request?: AuthenticatedDownloadRequest;
  documentRef?: Document;
  createObjectURL?: (blob: Blob) => string;
  revokeObjectURL?: (url: string) => void;
  scheduleRevoke?: (callback: () => void) => void;
}

export interface DownloadAuthenticatedFileResult {
  filename: string;
  objectUrl: string;
}

function resolveDownloadUrl(url: string): string {
  if (/^https?:\/\//i.test(url)) {
    return url;
  }
  if (!API_BASE) {
    return url;
  }
  const normalizedBase = API_BASE.replace(/\/$/, "");
  const normalizedPath = url.startsWith("/") ? url : `/${url}`;
  return `${normalizedBase}${normalizedPath}`;
}

function sanitizeFilename(filename: string): string {
  const trimmed = filename.trim().replace(/[\\/]+/g, "_");
  return trimmed || "download";
}

function parseContentDispositionFilename(header: string | null): string | null {
  if (!header) {
    return null;
  }

  const encodedMatch = header.match(/filename\*\s*=\s*([^;]+)/i);
  if (encodedMatch?.[1]) {
    const raw = encodedMatch[1].trim().replace(/^["']|["']$/g, "");
    const value = raw.replace(/^utf-8''/i, "");
    try {
      return sanitizeFilename(decodeURIComponent(value));
    } catch {
      return sanitizeFilename(value);
    }
  }

  const filenameMatch = header.match(/filename\s*=\s*(?:"([^"]+)"|([^;]+))/i);
  const filename = filenameMatch?.[1] ?? filenameMatch?.[2];
  return filename ? sanitizeFilename(filename) : null;
}

export async function downloadAuthenticatedFile(
  url: string,
  fallbackFilename: string,
  options: DownloadAuthenticatedFileOptions = {},
): Promise<DownloadAuthenticatedFileResult> {
  const request = options.request ?? authenticatedRequest;
  const documentRef =
    options.documentRef ??
    (typeof document !== "undefined" ? document : undefined);
  const createObjectURL =
    options.createObjectURL ?? URL.createObjectURL.bind(URL);
  const revokeObjectURL =
    options.revokeObjectURL ?? URL.revokeObjectURL.bind(URL);
  const scheduleRevoke =
    options.scheduleRevoke ??
    ((callback: () => void) => {
      setTimeout(callback, 0);
    });

  if (!documentRef) {
    throw new Error("Document is not available for file download");
  }

  const response = await request(resolveDownloadUrl(url), { method: "GET" });
  if (!response.ok) {
    throw new Error(`Download failed: ${response.status}`);
  }

  const blob = await response.blob();
  const filename =
    parseContentDispositionFilename(response.headers.get("Content-Disposition")) ??
    sanitizeFilename(fallbackFilename);
  const objectUrl = createObjectURL(blob);
  const anchor = documentRef.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  documentRef.body.appendChild(anchor);
  anchor.click();
  documentRef.body.removeChild(anchor);
  scheduleRevoke(() => revokeObjectURL(objectUrl));

  return { filename, objectUrl };
}
