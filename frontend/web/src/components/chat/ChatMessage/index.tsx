import { AuiIf, MessagePrimitive, getExternalStoreMessages, useAuiState } from "@assistant-ui/react";
import { memo } from "react";
import { AssistantMessageContent } from "../assistant-ui/elements/thread";
import { MessageTiming, type TimingStat } from "../assistant-ui/elements/message-timing";
import { AssistantUiMessageActions } from "../assistant-ui/MessageActions";
import type { Message } from "../../../types";
import { useTranslation } from "react-i18next";
import { AssistantUiMarkdownText, AssistantUiMessagePart, AssistantUiMessageStatus } from "../assistant-ui/MessageParts";
import { getAssistantUiDisplayParts } from "../assistant-ui/externalStoreRuntime";
import { UserMessageBubble } from "./UserMessageBubble";
import { createMessagePartRenderKeys } from "./MessagePartRenderer";
import { AssistantAvatar } from "./AssistantAvatar";
import { AgentIdentityAvatar } from "../../agent/AgentIdentityAvatar";
import type { AgentProfileAvatarRef } from "../../../types/agentProfile";
import { useModelCatalogContext } from "../../../contexts/ModelCatalogContext";
import { resolveTokenUsageModelDetails } from "./tokenUsageModel";
import { MessageWorkActivity } from "./MessageWorkActivity";
import type { RevealPreviewRequest } from "./items/revealPreviewData";
import type { RevealPreviewOpenSource } from "./items/revealPreviewState";
import { createMessageAnchorId } from "../../layout/AppContent/messageOutline";
import { formatDateTime, formatDateTimeShort } from "../../../utils/datetime";
import { selectAssistantCopyText } from "../../../types/assistantTextParts";
import {
  createArtifactDownloadScope,
  type ArtifactDownloadScopeContext,
} from "./items/artifactDownloadRegistry";

export interface AssistantPublicIdentity {
  agent_id: string;
  name: string;
  avatar_ref?: AgentProfileAvatarRef;
  avatar_seed?: string;
}

interface ChatMessageProps {
  artifactDownloadScopeContext?: ArtifactDownloadScopeContext;
  assistantIdentity?: AssistantPublicIdentity;
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

export const ChatMessage = memo(function ChatMessage({
  artifactDownloadScopeContext,
  assistantIdentity,
  onOpenPreview,
}: ChatMessageProps) {
  const { t } = useTranslation();
  const { availableModels } = useModelCatalogContext();
  const isUser = useAuiState((state) => state.message.role === "user");
  const isLastMessage = useAuiState((state) => state.message.isLast);
  // Read the business snapshot bound to this native message, so parts cannot cross render revisions.
  const message = useAuiState((state) => getExternalStoreMessages<Message>(state.message)[0]);
  if (!message) return null;
  const artifactDownloadScope = createArtifactDownloadScope(
    artifactDownloadScopeContext,
    message.id,
  );
  const modelDetails = resolveTokenUsageModelDetails({
    modelId: message.tokenUsage?.model_id,
    model: message.tokenUsage?.model,
    availableModels,
  });

  const timingStats: TimingStat[] = [];
  if (message.tokenUsage) {
    const usage = message.tokenUsage;
    timingStats.push(
      { label: t("chat.message.tokenInput"), value: usage.input_tokens.toLocaleString() },
      { label: t("chat.message.tokenOutput"), value: usage.output_tokens.toLocaleString() },
      { label: t("chat.message.tokenTotal"), value: usage.total_tokens.toLocaleString() },
    );
    if (usage.cache_creation_tokens) timingStats.push({ label: t("chat.message.tokenCacheCreation"), value: usage.cache_creation_tokens.toLocaleString() });
    if (usage.cache_read_tokens) timingStats.push({ label: t("chat.message.tokenCacheRead"), value: usage.cache_read_tokens.toLocaleString() });
  }
  if (message.duration !== undefined) timingStats.push({ label: t("chat.message.duration"), value: formatDurationMs(message.duration) });
  if (modelDetails) timingStats.push({ label: t("chat.message.model"), value: modelDetails.name });
  if (timingStats.length && message.timestamp) timingStats.push({ label: t("chat.message.startTime"), value: formatDateTime(message.timestamp) });

  // If there are visible parts, render in order; otherwise fall back to content.
  const visibleParts = getAssistantUiDisplayParts(message);
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
          messageId={message.id}
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
    return selectAssistantCopyText(
      hasParts ? visibleParts : message.parts,
      hasParts ? "" : message.content || "",
    );
  };

  // Assistant message: left layout
  return (
    <div
      id={createMessageAnchorId(message.id)}
      data-outline-anchor="true"
      data-outline-id={createMessageAnchorId(message.id)}
      className="group w-full animate-[fade-in_0.3s_ease-out] scroll-mt-6 rounded-lg transition-[background-color,box-shadow] duration-300 data-[external-navigation-highlighted=true]:bg-amber-50/85 data-[external-navigation-highlighted=true]:ring-2 data-[external-navigation-highlighted=true]:ring-amber-500/60 dark:data-[external-navigation-highlighted=true]:bg-amber-500/12 dark:data-[external-navigation-highlighted=true]:ring-amber-400/50"
    >
      <AssistantMessageContent>        {/* Content */}
        <div className="min-h-0 min-w-0 py-1">
          {/* Header: Avatar + Role label + Stop button */}
          <div className="mb-2 flex items-center gap-2">
            {assistantIdentity ? (
              <AgentIdentityAvatar
                agentId={assistantIdentity.agent_id}
                avatarRef={assistantIdentity.avatar_ref}
                avatarSeed={assistantIdentity.avatar_seed}
                name={assistantIdentity.name}
                size="xs"
              />
            ) : (
              <AssistantAvatar className="size-6 shrink-0 rounded-full" />
            )}
            <span
              className="text-sm font-semibold"
              style={{ color: "var(--theme-text)" }}
            >
              {assistantIdentity?.name ?? t("chat.message.assistant")}
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

          {hasParts ? (
            <div className="my-1.5 space-y-2">
              <MessageWorkActivity
                messageId={message.id}
                isStreaming={message.isStreaming}
                parts={visibleParts}
                partKeys={visiblePartKeys}
                renderPart={(part, index, withinWorkDetails) => (
                  <AssistantUiMessagePart
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
            <MessagePrimitive.Parts components={{ Text: AssistantUiMarkdownText }} unstable_showEmptyOnNonTextEnd={false} />
          )}
          <AssistantUiMessageStatus />
        </div>
        {/* Copy button and Token button - same line at bottom, show on message hover (only after message completes) */}
        <AuiIf condition={(state) => state.message.status?.type !== "running" && !state.message.metadata.custom.isSynchronizing}>
          <AssistantUiMessageActions messageId={message.id} text={getAssistantTextContent()}>
            <MessageTiming stats={timingStats} label={t("chat.message.tokenUsage")} />
          </AssistantUiMessageActions>
        </AuiIf>
      </AssistantMessageContent>
    </div>
  );
});
