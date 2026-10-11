import { LazyDocumentPreview } from "../../../documents/LazyDocumentPreview";
import type { RevealPreviewRequest } from "./revealPreviewData";

export function RevealPreviewHost({
  preview,
  onClose,
  onUserInteraction,
}: {
  preview: RevealPreviewRequest | null;
  onClose: () => void;
  onUserInteraction?: () => void;
}) {
  if (!preview) {
    return null;
  }

  if (preview.kind === "file") {
    return (
      <LazyDocumentPreview
        key={preview.previewKey}
        path={preview.filePath}
        content={preview.content}
        s3Key={preview.s3Key}
        signedUrl={preview.signedUrl}
        previewUrl={preview.previewUrl}
        downloadUrl={preview.downloadUrl}
        imageUrl={preview.imageUrl}
        fileSize={preview.fileSize}
        mimeType={preview.mimeType}
        onClose={onClose}
        onUserInteraction={onUserInteraction}
        registryKey={`reveal-preview:${preview.previewKey}`}
        mobileFillViewport
      />
    );
  }

  return null;
}
