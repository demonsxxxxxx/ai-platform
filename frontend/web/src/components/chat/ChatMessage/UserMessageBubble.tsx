import { useState } from "react";
import { clsx } from "clsx";
import { Copy, Check, GitBranch } from "lucide-react";
import { useTranslation } from "react-i18next";
import { AttachmentCard, ImageViewer } from "../../common";
import type { MessageAttachment } from "../../../types";
import { MarkdownContent } from "./MarkdownContent";
import { openAttachmentPreview } from "../attachmentPreviewStore";
import { getUserMessageActionButtonVisibilityClass } from "./userMessageBubbleState";
import { copyToClipboard } from "../../../utils/clipboard";
import { useSessionImageGallery } from "./sessionImageGallery";

// User message bubble component with copy support.
export function UserMessageBubble({
  content,
  attachments,
  onFork,
  isLastMessage,
}: {
  content?: string;
  attachments?: MessageAttachment[];
  onFork?: () => void;
  isLastMessage?: boolean;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const [imageViewerSrc, setImageViewerSrc] = useState<string | null>(null);
  const sessionImageGallery = useSessionImageGallery();

  const handleCopy = async () => {
    if (!content) return;
    await copyToClipboard(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Render attachment preview - use file card style uniformly
  const renderAttachments = () => {
    if (!attachments || attachments.length === 0) return null;

    return (
      <div className="flex flex-row justify-end flex-wrap gap-2 sm:gap-3 mb-2">
        {attachments.map((attachment) => {
          return (
            <AttachmentCard
              key={attachment.id}
              attachment={attachment}
              variant="preview"
              size="default"
              onClick={(previewSrc) => {
                if (previewSrc) {
                  sessionImageGallery?.openImage(previewSrc, attachment.name);
                  if (!sessionImageGallery) {
                    setImageViewerSrc(previewSrc);
                  }
                } else {
                  openAttachmentPreview(attachment, "user-message");
                }
              }}
            />
          );
        })}
      </div>
    );
  };

  const hasAttachments = attachments && attachments.length > 0;
  const hasContent = content && content.trim().length > 0;

  return (
    <div className="w-full px-2 py-3 sm:py-4 sm:px-4 group">
      <div className="mx-auto flex max-w-3xl lg:max-w-4xl xl:max-w-5xl justify-end px-2">
        <div className="flex flex-col items-end max-w-[90%]">
          {/* Attachment preview - outside message bubble */}
          {hasAttachments && renderAttachments()}

          {/* Message bubble */}
          {hasContent && (
            <div
              className="rounded-xl max-w-full px-4 py-2 border"
              style={{
                background:
                  "color-mix(in srgb, var(--theme-text-secondary) 8%, var(--theme-bg-card))",
                borderColor: "var(--theme-border)",
              }}
            >
              <div
                className="leading-relaxed text-[15px] sm:text-base"
                style={{ color: "var(--theme-text)" }}
              >
                <MarkdownContent content={content!} />
              </div>
            </div>
          )}

          {/* Action buttons - show on hover */}
          <div className="flex justify-end mt-2 gap-1">
            {onFork && (
              <button
                onClick={onFork}
                className={clsx(
                  "p-1.5 rounded-lg transition-colors duration-200",
                  getUserMessageActionButtonVisibilityClass(isLastMessage),
                  "hover:bg-black/5 dark:hover:bg-white/5",
                  "text-stone-400 dark:text-stone-500 hover:text-stone-600 dark:hover:text-stone-300",
                )}
                title={t("chat.message.fork")}
              >
                <GitBranch size={16} />
              </button>
            )}
            <button
              onClick={handleCopy}
              className={clsx(
                "p-1.5 rounded-lg transition-colors duration-200",
                getUserMessageActionButtonVisibilityClass(isLastMessage),
                "hover:bg-black/5 dark:hover:bg-white/5",
                copied
                  ? "text-emerald-500 dark:text-emerald-400"
                  : "text-stone-400 dark:text-stone-500 hover:text-stone-600 dark:hover:text-stone-300",
              )}
              title={copied ? t("chat.message.copied") : t("chat.message.copy")}
            >
              {copied ? <Check size={16} /> : <Copy size={16} />}
            </button>
          </div>
        </div>
      </div>

      {/* Image viewer for direct image preview */}
      {imageViewerSrc && (
        <ImageViewer
          src={imageViewerSrc}
          isOpen={!!imageViewerSrc}
          onClose={() => setImageViewerSrc(null)}
        />
      )}
    </div>
  );
}
