import { clsx } from "clsx";
import { useEffect, useRef, useState, memo } from "react";
import toast from "react-hot-toast";
import { Copy, Info, Sparkles } from "lucide-react";
import type {
  Message,
  MessagePart,
  TokenUsagePart,
} from "../../../types";
import { useTranslation } from "react-i18next";
import { MarkdownContent } from "./MarkdownContent";
import { UserMessageBubble } from "./UserMessageBubble";
import { createMessagePartRenderKeys, MessagePartRenderer } from "./MessagePartRenderer";
import { AssistantAvatar } from "./AssistantAvatar";
import { CollapsiblePill } from "../../common/CollapsiblePill";
import { useModelCatalogContext } from "../../../contexts/ModelCatalogContext";
import { ModelIconImg } from "../../agent/modelIcon.tsx";
import { shouldCloseTokenDetailsPopover } from "./tokenDetailsPopoverGuards";
import { resolveTokenUsageModelDetails } from "./tokenUsageModel";
import { getVisibleMessageParts } from "./messagePartVisibility";
import { MessageWorkActivity } from "./MessageWorkActivity";
import type { RevealPreviewRequest } from "./items/revealPreviewData";
import type { RevealPreviewOpenSource } from "./items/revealPreviewState";
import { createMessageAnchorId } from "../../layout/AppContent/messageOutline";
import { formatDateTime, formatDateTimeShort } from "../../../utils/datetime";
import { copyToClipboard } from "../../../utils/clipboard";
import {
  createArtifactDownloadScope,
  type ArtifactDownloadScopeContext,
} from "./items/artifactDownloadRegistry";

// Skeleton shown before the stream yields its first visible part.
function StreamingPlaceholder() {
  return (
    <div className="space-y-2.5 py-1 px-1">
      {/* First line - long bar */}
      <div className="skeleton-line w-full h-2 rounded-full" />

      {/* Second line - three medium bars */}
      <div className="flex gap-3">
        <div className="skeleton-line flex-1 h-2 rounded-full" />
        <div className="skeleton-line flex-1 h-2 rounded-full" />
        <div className="skeleton-line flex-1 h-2 rounded-full" />
      </div>

      {/* Third line - three medium bars */}
      <div className="flex gap-3">
        <div className="skeleton-line flex-1 h-2 rounded-full" />
        <div className="skeleton-line flex-1 h-2 rounded-full" />
        <div className="skeleton-line flex-1 h-2 rounded-full" />
      </div>

      {/* Fourth line */}
      <div className="flex gap-3">
        <div className="skeleton-line flex-1 h-2 rounded-full" />
        <div className="skeleton-line w-2/5 h-2 rounded-full" />
      </div>
    </div>
  );
}

interface ChatMessageProps {
  message: Message;
  artifactDownloadScopeContext?: ArtifactDownloadScopeContext;
  isLastMessage?: boolean;
  onOpenPreview?: (
    preview: RevealPreviewRequest,
    source?: RevealPreviewOpenSource,
  ) => boolean;
}

// Token usage statistics button component
function formatDurationMs(durationMs: number): string {
  return durationMs < 1000
    ? `${durationMs}毫秒`
    : `${(durationMs / 1000).toFixed(2)}秒`;
}

function TokenDetailsButton({
  tokenUsage,
  duration,
  timestamp,
  modelDetails,
  isLastMessage,
}: {
  tokenUsage?: TokenUsagePart;
  duration?: number;
  timestamp?: Date;
  modelDetails?: {
    name: string;
    value: string;
    provider?: string;
  } | null;
  isLastMessage?: boolean;
}) {
  const { t } = useTranslation();
  const [showDetails, setShowDetails] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);

  // Close details when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        shouldCloseTokenDetailsPopover(
          event.target as Node | null,
          buttonRef.current,
          popupRef.current,
        )
      ) {
        setShowDetails(false);
      }
    };
    if (showDetails) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [showDetails]);

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        onClick={() => setShowDetails(!showDetails)}
        className={clsx(
          "p-1.5 rounded-md transition-colors",
          !isLastMessage && "opacity-0 group-hover:opacity-100",
          "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]",
        )}
        title={t("chat.message.tokenUsage")}
      >
        <Info size={16} />
      </button>
      {/* Token details popup */}
      {showDetails && (
        <div
          ref={popupRef}
          className={clsx(
            "absolute bottom-full mb-2 left-0 z-50",
            "min-w-[150px] w-auto p-3 rounded-lg shadow-[0_12px_28px_rgba(15,23,42,0.12)]",
            "bg-[var(--theme-bg-card)] dark:bg-stone-900",
            "border border-[var(--theme-border)]",
            "whitespace-nowrap",
          )}
        >
          <div className="text-xs space-y-1.5">
            {tokenUsage && (
              <>
                <div className="flex justify-between gap-4 text-sky-600 dark:text-sky-400">
                  <span className="">{t("chat.message.tokenInput")}</span>
                  <span className="font-medium">
                    {tokenUsage.input_tokens?.toLocaleString()} tokens
                  </span>
                </div>
                <div className="flex justify-between gap-4 text-violet-600 dark:text-violet-400">
                  <span className="">{t("chat.message.tokenOutput")}</span>
                  <span className="font-medium">
                    {tokenUsage.output_tokens?.toLocaleString()} tokens
                  </span>
                </div>
                {(tokenUsage.cache_creation_tokens ?? 0) > 0 && (
                  <div className="flex justify-between gap-4 text-emerald-600 dark:text-emerald-400">
                    <span className="">
                      {t("chat.message.tokenCacheCreation")}
                    </span>
                    <span className="font-medium">
                      {(tokenUsage.cache_creation_tokens ?? 0).toLocaleString()}{" "}
                      tokens
                    </span>
                  </div>
                )}
                {(tokenUsage.cache_read_tokens ?? 0) > 0 && (
                  <div className="flex justify-between gap-4 text-pink-600 dark:text-pink-400">
                    <span className="">{t("chat.message.tokenCacheRead")}</span>
                    <span className="font-medium">
                      {(tokenUsage.cache_read_tokens ?? 0).toLocaleString()}{" "}
                      tokens
                    </span>
                  </div>
                )}
                <div className="mt-1.5 flex justify-between gap-4 border-t border-[var(--theme-border)] pt-1.5 text-amber-600 dark:text-amber-400">
                  <span className="">{t("chat.message.tokenTotal")}</span>
                  <span className="font-medium">
                    {tokenUsage.total_tokens?.toLocaleString()} tokens
                  </span>
                </div>
              </>
            )}
            {duration !== undefined && (
              <div className="mt-1.5 flex justify-between gap-4 border-t border-[var(--theme-border)] pt-1.5">
                <span className="text-[var(--theme-text-secondary)]">
                  {t("chat.message.duration")}
                </span>
                <span className="font-medium text-[var(--theme-text)]">
                  {formatDurationMs(duration)}
                </span>
              </div>
            )}
            {modelDetails && (
              <div className="mt-1.5 flex justify-between gap-4 border-t border-[var(--theme-border)] pt-1.5">
                <span className="text-[var(--theme-text-secondary)]">
                  {t("chat.message.model")}
                </span>
                <span className="flex items-center gap-1.5 font-medium text-[var(--theme-text)]">
                  <ModelIconImg
                    model={modelDetails.value}
                    provider={modelDetails.provider}
                    size={16}
                  />
                  <span>{modelDetails.name}</span>
                </span>
              </div>
            )}
            {timestamp && (
              <div className="mt-1.5 flex justify-between gap-4 border-t border-[var(--theme-border)] pt-1.5">
                <span className="text-[var(--theme-text-secondary)]">
                  {t("chat.message.startTime")}
                </span>
                <span className="font-medium tabular-nums text-[var(--theme-text)]">
                  {formatDateTime(timestamp)}
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export const ChatMessage = memo(function ChatMessage({
  message,
  artifactDownloadScopeContext,
  isLastMessage,
  onOpenPreview,
}: ChatMessageProps) {
  const { t } = useTranslation();
  const { availableModels } = useModelCatalogContext();
  const isUser = message.role === "user";
  const isStreaming = message.isStreaming && !message.content;
  const artifactDownloadScope = createArtifactDownloadScope(
    artifactDownloadScopeContext,
    message.id,
  );
  const modelDetails = resolveTokenUsageModelDetails({
    modelId: message.tokenUsage?.model_id,
    model: message.tokenUsage?.model,
    availableModels,
  });

  // If there are visible parts, render in order; otherwise fall back to content.
  const visibleParts = message.parts
    ? getVisibleMessageParts(message.parts)
    : [];
  const hasParts = visibleParts.length > 0;
  const visiblePartKeys = createMessagePartRenderKeys(message.id, visibleParts);
  // User message: bubble style, right aligned
  if (isUser) {
    return (
      <div
        id={createMessageAnchorId(message.id)}
        data-outline-anchor="true"
        data-outline-id={createMessageAnchorId(message.id)}
        className="space-y-3 scroll-mt-6 rounded-lg transition-[box-shadow] duration-300 data-[external-navigation-highlighted=true]:ring-2 data-[external-navigation-highlighted=true]:ring-amber-500/75 data-[external-navigation-highlighted=true]:shadow-[0_0_20px_rgba(245,158,11,0.2)] dark:data-[external-navigation-highlighted=true]:ring-amber-400/55 dark:data-[external-navigation-highlighted=true]:shadow-[0_0_20px_rgba(251,191,36,0.1)] sm:space-y-4"
      >
        <UserMessageBubble
          content={message.content}
          attachments={message.attachments}
          lockedSkillLabel={message.lockedSkillLabel}
          isLastMessage={isLastMessage}
        />
      </div>
    );
  }

  // Get assistant message's plain text content for copying
  const getAssistantTextContent = (): string => {
    if (hasParts) {
      // Extract all text content from parts
      return visibleParts
        .filter(
          (part): part is Extract<MessagePart, { type: "text" }> =>
            part.type === "text",
        )
        .map((part) => part.content)
        .join("\n");
    }
    return message.content || "";
  };

  // Assistant message: left layout
  return (
    <div
      id={createMessageAnchorId(message.id)}
      data-outline-anchor="true"
      data-outline-id={createMessageAnchorId(message.id)}
      className="group w-full animate-[fade-in_0.3s_ease-out] scroll-mt-6 rounded-lg transition-[background-color,box-shadow] duration-300 data-[external-navigation-highlighted=true]:bg-amber-50/85 data-[external-navigation-highlighted=true]:ring-2 data-[external-navigation-highlighted=true]:ring-amber-500/60 dark:data-[external-navigation-highlighted=true]:bg-amber-500/12 dark:data-[external-navigation-highlighted=true]:ring-amber-400/50"
    >
      <div className="mx-auto flex max-w-[68rem] flex-col px-3 sm:px-5">
        {/* Content */}
        <div className="min-h-0 min-w-0 py-1">
          {/* Header: Avatar + Role label + Stop button */}
          <div className="mb-2 flex items-center gap-2">
            <AssistantAvatar className="size-6 shrink-0 rounded-full" />
            <span
              className="text-sm font-semibold"
              style={{ color: "var(--theme-text)" }}
            >
              {t("chat.message.assistant")}
            </span>
            {message.timestamp && (
              <span
                className="text-xs ml-2 mt-0.5 tabular-nums opacity-0 group-hover:opacity-100 transition-opacity duration-200"
                style={{ color: "var(--theme-text-secondary)" }}
              >
                {message.timestamp
                  ? formatDateTimeShort(message.timestamp)
                  : ""}
              </span>
            )}
          </div>

          {/* Empty stream placeholder */}
          {isStreaming && !hasParts && <StreamingPlaceholder />}

          {hasParts ? (
            <div className="my-1.5 space-y-2">
              <MessageWorkActivity
                messageId={message.id}
                isStreaming={message.isStreaming}
                parts={visibleParts}
                partKeys={visiblePartKeys}
                renderPart={(part, index, withinWorkDetails) => (
                  <MessagePartRenderer
                    part={part}
                    messageId={message.id}
                    partIndex={index}
                    isStreaming={message.isStreaming}
                    isLast={index === visibleParts.length - 1}
                    onOpenPreview={onOpenPreview}
                    artifactDownloadScope={artifactDownloadScope}
                    withinWorkDetails={withinWorkDetails}
                  />
                )}
              />
            </div>
          ) : (
            <>
              {message.content && (
                <MarkdownContent
                  content={message.content}
                  isStreaming={message.isStreaming}
                  headingAnchorContext={{ messageId: message.id, partIndex: 0 }}
                />
              )}
            </>
          )}
          {/* Streaming indicator - bottom of message (when not showing thinking indicator) */}
          {message.isStreaming && !(isStreaming && !hasParts) && (
            <div className="mt-3 px-2">
              <CollapsiblePill
                status="loading"
                icon={<Sparkles size={12} className="shrink-0 opacity-50" />}
                label={t("chat.message.generating")}
                variant="tool"
                expandable={false}
                animatedDots
              />
            </div>
          )}
        </div>
        {/* Copy button and Token button - same line at bottom, show on message hover (only after message completes) */}
        {!message.isStreaming && (
          <div className="flex items-center gap-1">
            <button
              onClick={() => {
                const textContent = getAssistantTextContent();
                if (textContent) {
                  copyToClipboard(textContent);
                  toast.success(t("chat.message.copied"));
                }
              }}
              className={clsx(
                "p-1.5 rounded-md transition-colors",
                !isLastMessage && "opacity-0 group-hover:opacity-100",
                "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]",
              )}
              title={t("chat.message.copy")}
            >
              <Copy size={16} />
            </button>
            {/* Token usage statistics button */}
            {(message.tokenUsage || message.duration !== undefined) && (
              <TokenDetailsButton
                tokenUsage={message.tokenUsage}
                duration={message.duration}
                timestamp={message.timestamp}
                modelDetails={modelDetails}
                isLastMessage={isLastMessage}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
});
