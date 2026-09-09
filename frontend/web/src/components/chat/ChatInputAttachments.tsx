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
      className="mx-3 mt-2.5 -mb-1 flex gap-3 overflow-x-auto attachment-scroll pb-1"
      data-composer-file-reference-list
    >
      {attachments.map((attachment) => {
        const handleRemove = () => onRemoveAttachment(attachment);

        return (
          <div
            key={attachment.id}
            data-composer-file-reference={attachment.id}
            data-composer-file-state={
              attachment.isUploading ? "uploading" : "ready"
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
