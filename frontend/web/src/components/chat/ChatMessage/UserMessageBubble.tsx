import { useState } from "react";
import { clsx } from "clsx";
import { getUserMessageActionButtonVisibilityClass } from "./userMessageBubbleState";
import { AssistantUiMessageActions } from "../assistant-ui/MessageActions";
import { useTranslation } from "react-i18next";
import { ImageViewer } from "../../common";
import { MessageAttachments } from "../assistant-ui/MessageAttachments";
import type { MessageAttachment } from "../../../types";
import { MessagePrimitive } from "@assistant-ui/react";
import { UserMessageContent } from "../assistant-ui/elements/thread";
import { AssistantUiMarkdownText } from "../assistant-ui/MessageParts";
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
      <MessageAttachments attachments={attachments} className="mb-2 max-w-full"
        onOpen={(attachment, previewSrc) => {
          if (previewSrc) {
            sessionImageGallery?.openImage(previewSrc, attachment.name);
            if (!sessionImageGallery) setImageViewerSrc(previewSrc);
          } else {
            openAttachmentPreview(attachment, "user-message");
          }
        }} />
    );
  };

  const hasAttachments = attachments && attachments.length > 0;
  const hasContent = content && content.trim().length > 0;

  return (
    <div className="group w-full px-2 py-2 sm:px-4 sm:py-3">
      <div className="mx-auto flex max-w-[68rem] justify-end px-1 sm:px-2">
        <div className="flex min-w-0 max-w-[90%] flex-col items-end sm:max-w-[75%]" data-user-message-group>
          {lockedSkillLabel && (
            <div
              data-locked-skill-label={lockedSkillLabel}
              className="mb-2 rounded-full border border-sky-200/80 bg-sky-50 px-3 py-1 text-xs font-medium text-sky-700 dark:border-sky-900/60 dark:bg-sky-950/30 dark:text-sky-300"
            >
              {t("chat.message.lockedSkill", { skill: lockedSkillLabel })}
            </div>
          )}

          {/* One surface owns this message's files and text. */}
          {(hasAttachments || hasContent) && (
            <UserMessageContent className="max-w-full">
              {renderAttachments()}
              {hasContent && (
                <div className="break-words">
                  <MessagePrimitive.Parts components={{ Text: AssistantUiMarkdownText }} unstable_showEmptyOnNonTextEnd={false} />
                </div>
              )}
            </UserMessageContent>
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
