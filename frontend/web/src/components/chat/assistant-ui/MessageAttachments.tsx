import { Button } from "@lobehub/ui/base-ui";
import { FileTextIcon, ImageIcon, PaperclipIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { formatFileSize } from "../../common/AttachmentCard";
import { useSafeAttachmentImageSrc } from "../../common/attachmentImageSafety";
import type { MessageAttachment } from "../../../types";

export interface MessageAttachmentsProps {
  attachments: readonly MessageAttachment[];
  onOpen?: (attachment: MessageAttachment, previewSrc?: string) => void;
  className?: string;
}

function MessageAttachmentRow({
  attachment,
  onOpen,
}: {
  attachment: MessageAttachment;
  onOpen?: MessageAttachmentsProps["onOpen"];
}) {
  const previewSrc =
    useSafeAttachmentImageSrc(attachment.url, attachment.mimeType) ?? undefined;
  const { t } = useTranslation();
  const isImage = Boolean(previewSrc);
  const kind = attachment.type === "document" ? "document" : "file";
  const content: ReactNode = isImage ? (
    <>
      <span
        aria-hidden="true"
        className="flex size-12 shrink-0 items-center justify-center overflow-hidden rounded-lg"
      >
        <img
          src={previewSrc}
          alt=""
          className="h-full w-full object-cover"
        />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5 pe-2">
        <span className="truncate text-[13.5px] text-[var(--theme-text)]">
          {attachment.name}
        </span>
        <span className="text-xs text-[var(--theme-text-secondary)]">
          {formatFileSize(attachment.size)}
        </span>
      </span>
      <ImageIcon
        aria-hidden="true"
        className="me-2 size-3.5 shrink-0 text-[var(--theme-text-secondary)]"
      />
    </>
  ) : (
    <>
      <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-card)] text-[var(--theme-text-secondary)]">
        {kind === "document" ? (
          <FileTextIcon aria-hidden="true" className="size-3.5" />
        ) : (
          <PaperclipIcon aria-hidden="true" className="size-3.5" />
        )}
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-[13.5px] text-[var(--theme-text)]">
          {attachment.name}
        </span>
        <span className="text-xs text-[var(--theme-text-secondary)]">
          {formatFileSize(attachment.size)}
        </span>
      </span>
    </>
  );

  const className = isImage
    ? "group flex w-full items-center gap-3 overflow-hidden rounded-2xl border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-2 text-start"
    : "flex w-full items-center gap-2.5 rounded-xl border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3 py-2 text-start";

  return onOpen ? (
    <Button
      type="text"
      htmlType="button"
      data-slot="message-attachment"
      onClick={() => onOpen(attachment, previewSrc)}
      aria-label={`${isImage ? t("imageViewer.fullscreen") : t("fileUpload.selectFile")} ${attachment.name}`}
      style={{
        height: "auto",
        minHeight: 40,
        width: "100%",
        justifyContent: "flex-start",
        padding: isImage ? 0 : "8px 12px",
        textAlign: "left",
      }}
      className={`${className} transition-colors hover:bg-[var(--theme-bg-sidebar)]`}
    >
      {content}
    </Button>
  ) : (
    <div data-slot="message-attachment" className={className}>
      {content}
    </div>
  );
}

/**
 * Props-driven received-file presentation adapted from assistant-ui's
 * MessageAttachments element. It never owns upload, authorization, or preview
 * lifetime; those remain platform responsibilities.
 */
export function MessageAttachments({
  attachments,
  onOpen,
  className,
}: MessageAttachmentsProps) {
  if (attachments.length === 0) return null;
  return (
    <div
      data-slot="message-attachments"
      className={`flex w-full max-w-sm flex-col gap-1.5 ${className ?? ""}`}
    >
      {attachments.map((attachment) => (
        <MessageAttachmentRow
          key={attachment.id}
          attachment={attachment}
          onOpen={onOpen}
        />
      ))}
    </div>
  );
}
