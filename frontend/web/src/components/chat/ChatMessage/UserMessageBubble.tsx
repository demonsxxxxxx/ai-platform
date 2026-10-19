import { useState } from "react";
import { clsx } from "clsx";
import { getUserMessageActionButtonVisibilityClass } from "./userMessageBubbleState";
import { AssistantUiMessageActions } from "../assistant-ui/MessageActions";
import { useTranslation } from "react-i18next";
import { AttachmentCard, ImageViewer } from "../../common";
import type { MessageAttachment } from "../../../types";
import { MarkdownContent } from "./MarkdownContent";
import { openAttachmentPreview } from "../attachmentPreviewStore";

import { useSessionImageGallery } from "./sessionImageGallery";

// User message bubble component (with copy function, supports markdown rendering)
export function UserMessageBubble({
  messageId,
  content,
  attachments,
  lockedSkillLabel,
  isLastMessage,
}: {
  messageId: string;
  content?: string;
  attachments?: MessageAttachment[];
  lockedSkillLabel?: string;
  isLastMessage?: boolean;
}) {
  const { t } = useTranslation();
  const [imageViewerSrc, setImageViewerSrc] = useState<string | null>(null);
  const sessionImageGallery = useSessionImageGallery();

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
    <div className="group w-full px-2 py-2 sm:px-4 sm:py-3">
      <div className="mx-auto flex max-w-[68rem] justify-end px-1 sm:px-2">
        <div className="flex max-w-[90%] flex-col items-end sm:max-w-[75%]">
          {/* Attachment preview - outside message bubble */}
          {hasAttachments && renderAttachments()}

          {lockedSkillLabel && (
            <div
              data-locked-skill-label={lockedSkillLabel}
              className="mb-2 rounded-full border border-sky-200/80 bg-sky-50 px-3 py-1 text-xs font-medium text-sky-700 dark:border-sky-900/60 dark:bg-sky-950/30 dark:text-sky-300"
            >
              {t("chat.message.lockedSkill", { skill: lockedSkillLabel })}
            </div>
          )}

          {/* Message bubble */}
          {hasContent && (
            <div
              className="max-w-full rounded-lg border px-4 py-2 shadow-[0_4px_12px_rgba(18,38,63,0.03)]"
              style={{
                background:
                  "color-mix(in srgb, var(--theme-primary-light) 42%, var(--theme-bg-card))",
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

          <div className={clsx("mt-1 flex justify-end", getUserMessageActionButtonVisibilityClass(isLastMessage))}>
            <AssistantUiMessageActions messageId={messageId} text={content ?? ""} />
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
