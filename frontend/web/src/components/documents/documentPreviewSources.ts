import {
  downloadAuthenticatedFile as defaultDownloadAuthenticatedFile,
  type DownloadAuthenticatedFileResult,
} from "../../services/api/download";
import {
  fetchDocumentArrayBuffer,
  isUnsafeExternalHttpDocumentUrl,
  isUnsafeUnauthenticatedDocumentUrl,
  shouldUseAuthenticatedDocumentRequest,
  type DocumentFetchOptions,
} from "./documentFetchCache";
import { isSensitiveInternalPath } from "./documentUrlSafety";

type NativeFetch = typeof fetch;
type OpenWindow = (
  url: string,
  target?: string,
  features?: string,
) => Window | null;
type CreateObjectURL = (blob: Blob) => string;
type RevokeObjectURL = (url: string) => void;
type DownloadAuthenticatedFile = (
  url: string,
  fallbackFilename: string,
) => Promise<DownloadAuthenticatedFileResult>;

interface PreviewSourceOptions {
  fetchOptions?: DocumentFetchOptions;
  createObjectURL?: CreateObjectURL;
}

export interface DownloadPreviewUrlOptions {
  url: string;
  fileName: string;
  fetchOptions?: Pick<DocumentFetchOptions, "currentOrigin" | "apiBase">;
  downloadAuthenticatedFile?: DownloadAuthenticatedFile;
  fetchImpl?: NativeFetch;
  openWindow?: OpenWindow;
  documentRef?: Document;
  createObjectURL?: CreateObjectURL;
  revokeObjectURL?: RevokeObjectURL;
}

export interface OpenPreviewUrlOptions {
  url: string;
  fileName?: string | null;
  mimeType?: string | null;
  fetchOptions?: DocumentFetchOptions;
  openWindow?: OpenWindow;
  createObjectURL?: CreateObjectURL;
  revokeObjectURL?: RevokeObjectURL;
  revokeDelayMs?: number;
}

const ACTIVE_PREVIEW_MIME_TYPES = new Set([
  "application/xhtml+xml",
  "application/xml",
  "image/svg+xml",
  "text/html",
  "text/xml",
]);

const ACTIVE_PREVIEW_EXTENSIONS = new Set([
  "htm",
  "html",
  "mhtml",
  "shtml",
  "svg",
  "xhtml",
  "xml",
]);

function getDefaultCreateObjectURL(): CreateObjectURL {
  return URL.createObjectURL.bind(URL);
}

function getDefaultRevokeObjectURL(): RevokeObjectURL {
  return URL.revokeObjectURL.bind(URL);
}

function getDefaultDocument(): Document | undefined {
  return typeof document === "undefined" ? undefined : document;
}

function getDefaultOpenWindow(): OpenWindow | undefined {
  return typeof window === "undefined" ? undefined : window.open.bind(window);
}

function normalizeMimeType(value?: string | null): string {
  return value?.split(";", 1)[0]?.trim().toLowerCase() ?? "";
}

function getPathExtension(value?: string | null): string {
  if (!value) {
    return "";
  }
  const [path] = value.split(/[?#]/, 1);
  const normalized = decodeURIComponent(path).replace(/\\/g, "/");
  const segment = normalized.split("/").pop() ?? "";
  const lastDot = segment.lastIndexOf(".");
  return lastDot <= 0 ? "" : segment.slice(lastDot + 1).toLowerCase();
}

function isPotentiallyActivePreviewContent(input: {
  url: string;
  fileName?: string | null;
  mimeType?: string | null;
}): boolean {
  if (ACTIVE_PREVIEW_MIME_TYPES.has(normalizeMimeType(input.mimeType))) {
    return true;
  }
  if (ACTIVE_PREVIEW_EXTENSIONS.has(getPathExtension(input.fileName))) {
    return true;
  }
  return ACTIVE_PREVIEW_EXTENSIONS.has(getPathExtension(input.url));
}

function shouldFailClosedOpaqueAuthenticatedOpen(
  input: OpenPreviewUrlOptions,
): boolean {
  return (
    shouldUseAuthenticatedDocumentRequest(input.url, input.fetchOptions) &&
    !normalizeMimeType(input.mimeType) &&
    !getPathExtension(input.fileName) &&
    !getPathExtension(input.url)
  );
}

async function createPlainTextDocumentObjectUrl(input: {
  url: string;
  fetchOptions?: DocumentFetchOptions;
  createObjectURL?: CreateObjectURL;
}): Promise<string> {
  const buffer = await fetchDocumentArrayBuffer(input.url, input.fetchOptions);
  const blob = new Blob([buffer], {
    type: "text/plain;charset=utf-8",
  });
  const createObjectURL = input.createObjectURL ?? getDefaultCreateObjectURL();
  return createObjectURL(blob);
}

export async function createDocumentObjectUrl(input: {
  url: string;
  mimeType?: string | null;
  fetchOptions?: DocumentFetchOptions;
  createObjectURL?: CreateObjectURL;
}): Promise<string> {
  const buffer = await fetchDocumentArrayBuffer(input.url, input.fetchOptions);
  const blob = new Blob([buffer], {
    type: input.mimeType || "application/octet-stream",
  });
  const createObjectURL = input.createObjectURL ?? getDefaultCreateObjectURL();
  return createObjectURL(blob);
}

export async function resolveDocumentPreviewUrl(input: {
  url: string;
  mimeType?: string | null;
} & PreviewSourceOptions): Promise<string> {
  if (isSensitiveInternalPath(input.url)) {
    return "";
  }

  if (shouldUseAuthenticatedDocumentRequest(input.url, input.fetchOptions)) {
    return createDocumentObjectUrl(input);
  }

  return isUnsafeExternalHttpDocumentUrl(input.url, input.fetchOptions) ||
    isUnsafeUnauthenticatedDocumentUrl(input.url, input.fetchOptions)
    ? ""
    : input.url;
}

export function resolvePptPreviewBuffer(input: {
  url: string;
  fetchOptions?: DocumentFetchOptions;
}): Promise<ArrayBuffer> {
  if (isSensitiveInternalPath(input.url)) {
    return Promise.reject(new Error("Unsafe internal preview URL"));
  }

  if (isUnsafeExternalHttpDocumentUrl(input.url, input.fetchOptions)) {
    return Promise.reject(new Error("Unsafe external http preview URL"));
  }
  if (isUnsafeUnauthenticatedDocumentUrl(input.url, input.fetchOptions)) {
    return Promise.reject(new Error("Unsafe unauthenticated preview URL"));
  }

  return fetchDocumentArrayBuffer(input.url, input.fetchOptions);
}

async function downloadNativeUrl(input: {
  url: string;
  fileName: string;
  fetchImpl: NativeFetch;
  openWindow?: OpenWindow;
  documentRef?: Document;
  createObjectURL: CreateObjectURL;
  revokeObjectURL: RevokeObjectURL;
}): Promise<void> {
  try {
    const response = await input.fetchImpl(input.url);
    if (!response.ok) {
      throw new Error(`Failed to download file: ${response.status}`);
    }
    const blob = await response.blob();
    const blobUrl = input.createObjectURL(blob);
    const documentRef = input.documentRef ?? getDefaultDocument();
    if (!documentRef) {
      throw new Error("Document is not available for file download");
    }
    const anchor = documentRef.createElement("a");
    anchor.href = blobUrl;
    anchor.download = input.fileName;
    documentRef.body.appendChild(anchor);
    anchor.click();
    documentRef.body.removeChild(anchor);
    input.revokeObjectURL(blobUrl);
  } catch {
    const openWindow = input.openWindow ?? getDefaultOpenWindow();
    openWindow?.(input.url, "_blank");
  }
}

export async function downloadPreviewUrl(
  input: DownloadPreviewUrlOptions,
): Promise<void> {
  if (isSensitiveInternalPath(input.url)) {
    return;
  }

  if (shouldUseAuthenticatedDocumentRequest(input.url, input.fetchOptions)) {
    const downloadAuthenticatedFile =
      input.downloadAuthenticatedFile ?? defaultDownloadAuthenticatedFile;
    await downloadAuthenticatedFile(input.url, input.fileName);
    return;
  }

  if (isUnsafeExternalHttpDocumentUrl(input.url, input.fetchOptions)) {
    return;
  }
  if (isUnsafeUnauthenticatedDocumentUrl(input.url, input.fetchOptions)) {
    return;
  }

  await downloadNativeUrl({
    url: input.url,
    fileName: input.fileName,
    fetchImpl: input.fetchImpl ?? fetch,
    openWindow: input.openWindow,
    documentRef: input.documentRef,
    createObjectURL: input.createObjectURL ?? getDefaultCreateObjectURL(),
    revokeObjectURL: input.revokeObjectURL ?? getDefaultRevokeObjectURL(),
  });
}

export async function openPreviewUrl(
  input: OpenPreviewUrlOptions,
): Promise<void> {
  if (isSensitiveInternalPath(input.url)) {
    return;
  }

  const openWindow = input.openWindow ?? getDefaultOpenWindow();
  if (!openWindow) {
    return;
  }

  if (isUnsafeExternalHttpDocumentUrl(input.url, input.fetchOptions)) {
    return;
  }
  if (isUnsafeUnauthenticatedDocumentUrl(input.url, input.fetchOptions)) {
    return;
  }

  if (
    isPotentiallyActivePreviewContent(input) ||
    shouldFailClosedOpaqueAuthenticatedOpen(input)
  ) {
    const objectUrl = await createPlainTextDocumentObjectUrl({
      url: input.url,
      fetchOptions: input.fetchOptions,
      createObjectURL: input.createObjectURL,
    });
    openWindow(objectUrl, "_blank", "noopener noreferrer");
    const revokeObjectURL =
      input.revokeObjectURL ?? getDefaultRevokeObjectURL();
    setTimeout(
      () => revokeObjectURL(objectUrl),
      input.revokeDelayMs ?? 30_000,
    );
    return;
  }

  if (shouldUseAuthenticatedDocumentRequest(input.url, input.fetchOptions)) {
    const objectUrl = await createDocumentObjectUrl({
      url: input.url,
      mimeType: input.mimeType,
      fetchOptions: input.fetchOptions,
      createObjectURL: input.createObjectURL,
    });
    openWindow(objectUrl, "_blank", "noopener noreferrer");
    const revokeObjectURL =
      input.revokeObjectURL ?? getDefaultRevokeObjectURL();
    setTimeout(
      () => revokeObjectURL(objectUrl),
      input.revokeDelayMs ?? 30_000,
    );
    return;
  }

  openWindow(input.url, "_blank", "noopener noreferrer");
}
