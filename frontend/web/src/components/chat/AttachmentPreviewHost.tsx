import { useSyncExternalStore } from "react";
import { DelayedUnmount } from "../common/DelayedUnmount";
import { useSafeAttachmentImageSrc } from "../common/attachmentImageSafety";
import { isAllowedAuthenticatedArtifactFileUrl } from "../documents/documentUrlSafety";
import { LazyDocumentPreview } from "../documents/LazyDocumentPreview";
import {
  closeAttachmentPreview,
  getAttachmentPreviewState,
  subscribeAttachmentPreview,
} from "./attachmentPreviewStore";

export function AttachmentPreviewHost() {
  const previewState = useSyncExternalStore(
    subscribeAttachmentPreview,
    getAttachmentPreviewState,
    () => null,
  );
  const attachment = previewState?.attachment ?? null;
  const authenticatedUrl =
    attachment?.url && isAllowedAuthenticatedArtifactFileUrl(attachment.url)
      ? attachment.url
      : undefined;
  const safeImageUrl =
    useSafeAttachmentImageSrc(
      authenticatedUrl ? undefined : attachment?.url,
      attachment?.mimeType,
    ) ?? null;

  return (
    <DelayedUnmount show={!!attachment}>
      {attachment && (
        <LazyDocumentPreview
          path={attachment.name}
          s3Key={authenticatedUrl ? undefined : attachment.key}
          signedUrl={authenticatedUrl}
          downloadUrl={attachment.downloadUrl}
          fileSize={attachment.size}
          mimeType={attachment.mimeType}
          registryKey={`attachment-preview:${
            previewState?.source ?? "unknown"
          }:${attachment.key}`}
          imageUrl={safeImageUrl ?? undefined}
          onClose={closeAttachmentPreview}
          mobileFillViewport
        />
      )}
    </DelayedUnmount>
  );
}
