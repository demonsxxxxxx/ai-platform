import { useLayoutEffect, useRef, useState } from "react";
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
  const stripRef = useRef<HTMLDivElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });
  const hasAttachments = attachments.length > 0;

  useLayoutEffect(() => {
    const strip = stripRef.current;
    const row = rowRef.current;
    if (!strip || !row) return;
    const update = () => {
      const left = strip.scrollLeft > 1;
      const right = strip.scrollWidth - strip.clientWidth - strip.scrollLeft > 1;
      setEdges((current) => current.left === left && current.right === right ? current : { left, right });
    };
    const resize = new ResizeObserver(update);
    resize.observe(strip);
    resize.observe(row);
    strip.addEventListener("scroll", update, { passive: true });
    update();
    return () => {
      resize.disconnect();
      strip.removeEventListener("scroll", update);
    };
  }, [hasAttachments]);

  if (!hasAttachments) return null;

  return (
    <div className="composer-attachment-edges" data-more-left={edges.left} data-more-right={edges.right}>
      <div
        ref={stripRef}
        data-slot="composer-attachments"
        className="composer-attachment-strip min-w-0 overflow-x-auto"
        data-composer-file-reference-list
      >
        <div ref={rowRef} className="flex w-max gap-2 px-1.5 py-1.5">
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
      </div>
    </div>
  );
}
