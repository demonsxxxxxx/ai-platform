import { registerAuthScopedCacheClearer } from "../../services/api/authCacheInvalidation";
import { authenticatedRequest as defaultAuthenticatedRequest } from "../../services/api/authenticatedRequest";
import {
  isAllowedAuthenticatedArtifactFileUrl,
  isSensitiveInternalPath,
} from "./documentUrlSafety";

const textCache = new Map<string, Promise<string>>();
const arrayBufferCache = new Map<string, Promise<ArrayBuffer>>();

export type DocumentFetchRequest = typeof fetch;

export interface DocumentFetchOptions {
  authenticatedRequest?: DocumentFetchRequest;
  fetchImpl?: DocumentFetchRequest;
  currentOrigin?: string;
  apiBase?: string;
}

function getCurrentOrigin(): string | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  return window.location.origin;
}

function hasAbsoluteScheme(url: string): boolean {
  return /^[a-z][a-z\d+.-]*:/i.test(url) || url.startsWith("//");
}

function parseDocumentUrl(
  url: string,
  currentOrigin: string | undefined,
): URL | null {
  try {
    if (hasAbsoluteScheme(url)) {
      return new URL(url, currentOrigin);
    }
    if (currentOrigin) {
      return new URL(url, currentOrigin);
    }
  } catch {
    return null;
  }
  return null;
}

export function isUnsafeExternalHttpDocumentUrl(
  url: string,
  options: Pick<DocumentFetchOptions, "currentOrigin"> = {},
): boolean {
  const trimmed = url.trim();
  if (!trimmed || !hasAbsoluteScheme(trimmed)) return false;

  const currentOrigin = options.currentOrigin ?? getCurrentOrigin();
  if (trimmed.startsWith("//") && !currentOrigin) {
    return true;
  }

  const parsedUrl = parseDocumentUrl(trimmed, currentOrigin);
  if (!parsedUrl || parsedUrl.protocol !== "http:") {
    return false;
  }
  if (!currentOrigin) {
    return true;
  }

  try {
    return parsedUrl.origin !== new URL(currentOrigin).origin;
  } catch {
    return true;
  }
}

export function shouldUseAuthenticatedDocumentRequest(
  url: string,
  options: Pick<DocumentFetchOptions, "currentOrigin" | "apiBase"> = {},
): boolean {
  const currentOrigin = options.currentOrigin ?? getCurrentOrigin();
  return isAllowedAuthenticatedArtifactFileUrl(url, { currentOrigin });
}

function isApiDocumentUrl(
  url: string,
  options: Pick<DocumentFetchOptions, "currentOrigin"> = {},
): boolean {
  const trimmed = url.trim();
  if (trimmed.startsWith("/api/")) {
    return true;
  }

  const currentOrigin = options.currentOrigin ?? getCurrentOrigin();
  const parsedUrl = parseDocumentUrl(trimmed, currentOrigin);
  return parsedUrl?.pathname.startsWith("/api/") ?? false;
}

export function isUnsafeUnauthenticatedDocumentUrl(
  url: string,
  options: Pick<DocumentFetchOptions, "currentOrigin" | "apiBase"> = {},
): boolean {
  const trimmed = url.trim();
  if (!trimmed || shouldUseAuthenticatedDocumentRequest(trimmed, options)) {
    return false;
  }

  return hasAbsoluteScheme(trimmed) || isApiDocumentUrl(trimmed, options);
}

function selectDocumentRequest(
  url: string,
  options: DocumentFetchOptions,
): DocumentFetchRequest {
  if (shouldUseAuthenticatedDocumentRequest(url, options)) {
    return options.authenticatedRequest ?? defaultAuthenticatedRequest;
  }
  return options.fetchImpl ?? fetch;
}

async function fetchWithValidation(
  url: string,
  options: DocumentFetchOptions = {},
): Promise<Response> {
  if (isSensitiveInternalPath(url)) {
    throw new Error("Unsafe internal preview URL");
  }

  if (isUnsafeExternalHttpDocumentUrl(url, options)) {
    throw new Error("Unsafe external http preview URL");
  }
  if (isUnsafeUnauthenticatedDocumentUrl(url, options)) {
    throw new Error("Unsafe unauthenticated preview URL");
  }

  const request = selectDocumentRequest(url, options);
  const response = await request(url, { method: "GET" });
  if (!response.ok) {
    throw new Error(`Failed to fetch file: ${response.status}`);
  }
  return response;
}

export function fetchDocumentText(
  url: string,
  options: DocumentFetchOptions = {},
): Promise<string> {
  if (shouldUseAuthenticatedDocumentRequest(url, options)) {
    return fetchWithValidation(url, options).then((response) =>
      response.text(),
    );
  }

  const cached = textCache.get(url);
  if (cached) {
    return cached;
  }

  const request = fetchWithValidation(url, options)
    .then((response) => response.text())
    .catch((error) => {
      textCache.delete(url);
      throw error;
    });

  textCache.set(url, request);
  return request;
}

export function fetchDocumentArrayBuffer(
  url: string,
  options: DocumentFetchOptions = {},
): Promise<ArrayBuffer> {
  if (shouldUseAuthenticatedDocumentRequest(url, options)) {
    return fetchWithValidation(url, options).then((response) =>
      response.arrayBuffer(),
    );
  }

  const cached = arrayBufferCache.get(url);
  if (cached) {
    return cached;
  }

  const request = fetchWithValidation(url, options)
    .then((response) => response.arrayBuffer())
    .catch((error) => {
      arrayBufferCache.delete(url);
      throw error;
    });

  arrayBufferCache.set(url, request);
  return request;
}

export function clearDocumentFetchCaches(): void {
  textCache.clear();
  arrayBufferCache.clear();
}

registerAuthScopedCacheClearer(clearDocumentFetchCaches);
