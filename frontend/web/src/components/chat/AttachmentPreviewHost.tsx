import { useEffect, useRef, useState } from "react";
import { DelayedUnmount } from "../common/DelayedUnmount";
import { useSafeAttachmentImageSrc } from "../common/attachmentImageSafety";
import { LazyDocumentPreview } from "../documents/LazyDocumentPreview";
import {
  closeAttachmentPreview,
  getAttachmentPreviewState,
  subscribeAttachmentPreview,
} from "./attachmentPreviewStore";

export function AttachmentPreviewHost() {
  const [, forceRender] = useState(0);
  const previewStateRef = useRef(getAttachmentPreviewState());

  useEffect(() => {
    const syncPreviewState = () => {
      previewStateRef.current = getAttachmentPreviewState();
      forceRender((count) => count + 1);
    };

    return subscribeAttachmentPreview(syncPreviewState);
  }, []);

  const previewState = previewStateRef.current;
  const attachment = previewState?.attachment ?? null;
  const safeImageUrl =
    useSafeAttachmentImageSrc(attachment?.url, attachment?.mimeType) ?? null;

  return (
    <DelayedUnmount show={!!attachment}>
      {attachment && (
        <LazyDocumentPreview
          path={attachment.name}
          s3Key={attachment.key}
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
