import { AttachmentCard } from "../common/AttachmentCard";
import { openAttachmentPreview } from "./attachmentPreviewStore";
import type { MessageAttachment } from "../../types";

interface ChatInputAttachmentsProps {
  attachments: MessageAttachment[];
  onRemoveAttachment: (attachment: MessageAttachment) => void;
  onCancelUpload: (id: string) => void;
  onImageViewerOpen: (url: string) => void;
}

export function ChatInputAttachments({
  attachments,
  onRemoveAttachment,
  onCancelUpload,
  onImageViewerOpen,
}: ChatInputAttachmentsProps) {
  if (attachments.length === 0) return null;

  return (
    <div
      data-slot="composer-attachments"
      className="mx-3 mt-3 flex min-w-0 gap-2 overflow-x-auto attachment-scroll pb-2"
      data-composer-file-reference-list
    >
      {attachments.map((attachment) => {
        const handleRemove = () => onRemoveAttachment(attachment);

        return (
          <div
            key={attachment.id}
            className="min-w-0 shrink-0"
            data-slot="composer-attachment"
            data-composer-file-reference={attachment.id}
            data-composer-file-state={
              attachment.isUploading
                ? (attachment.uploadStatus ?? "uploading")
                : "ready"
            }
            data-composer-file-type={attachment.type}
          >
            <AttachmentCard
              attachment={attachment}
              variant="editable"
              size="compact"
              isUploading={attachment.isUploading}
              onClick={(previewSrc) => {
                if (previewSrc) {
                  onImageViewerOpen(previewSrc);
                } else {
                  openAttachmentPreview(attachment, "chat-input");
                }
              }}
              onRemove={handleRemove}
              onCancel={
                attachment.isUploading
                  ? () => onCancelUpload(attachment.id)
                  : undefined
              }
            />
          </div>
        );
      })}
    </div>
  );
}
