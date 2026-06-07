import { useEffect, useState } from "react";
import { getFullUrl } from "../../services/api/config";
import {
  createDocumentObjectUrl,
  type DownloadPreviewUrlOptions,
} from "../documents/documentPreviewSources";
import type { DocumentFetchOptions } from "../documents/documentFetchCache";
import {
  isAllowedAuthenticatedArtifactFileUrl,
  isSensitiveInternalPath,
} from "../documents/documentUrlSafety";

type CreateObjectURL = NonNullable<DownloadPreviewUrlOptions["createObjectURL"]>;

interface SafeAttachmentImageObjectUrlOptions {
  fetchOptions?: DocumentFetchOptions;
  createObjectURL?: CreateObjectURL;
}

export function resolveSafeAttachmentImageSrc(
  src: string | undefined | null,
): string | null {
  if (!src) return null;
  const trimmed = src.trim();
  if (!trimmed || isSensitiveInternalPath(trimmed)) return null;
  if (!isAllowedAuthenticatedArtifactFileUrl(trimmed)) return null;

  const resolved = getFullUrl(trimmed) || trimmed;
  if (
    isSensitiveInternalPath(resolved) ||
    !isAllowedAuthenticatedArtifactFileUrl(resolved)
  ) {
    return null;
  }

  return resolved;
}

export async function createSafeAttachmentObjectUrl(
  src: string | undefined | null,
  mimeType?: string | null,
  options: SafeAttachmentImageObjectUrlOptions = {},
): Promise<string | null> {
  const safeSrc = resolveSafeAttachmentImageSrc(src);
  if (!safeSrc) return null;

  return createDocumentObjectUrl({
    url: safeSrc,
    mimeType,
    fetchOptions: options.fetchOptions,
    createObjectURL: options.createObjectURL,
  });
}

export const createSafeAttachmentImageObjectUrl =
  createSafeAttachmentObjectUrl;

function revokeBlobUrl(url: string | null): void {
  if (!url?.startsWith("blob:")) return;
  URL.revokeObjectURL(url);
}

export function useSafeAttachmentObjectUrl(
  src: string | undefined | null,
  mimeType?: string | null,
  enabled = true,
): string | null {
  const [imageSrc, setImageSrc] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;

    setImageSrc(null);
    if (!enabled) {
      return () => {
        active = false;
      };
    }

    void createSafeAttachmentObjectUrl(src, mimeType)
      .then((url) => {
        if (!active) {
          revokeBlobUrl(url);
          return;
        }
        objectUrl = url;
        setImageSrc(url);
      })
      .catch(() => {
        if (active) {
          setImageSrc(null);
        }
      });

    return () => {
      active = false;
      revokeBlobUrl(objectUrl);
    };
  }, [enabled, mimeType, src]);

  return imageSrc;
}

export function useSafeAttachmentImageSrc(
  src: string | undefined | null,
  mimeType?: string | null,
): string | null {
  return useSafeAttachmentObjectUrl(src, mimeType, !!mimeType?.startsWith("image/"));
}
