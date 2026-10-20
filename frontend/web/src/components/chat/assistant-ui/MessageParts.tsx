import { AuiIf, MessagePartPrimitive, MessagePrimitive, useAuiState } from "@assistant-ui/react";
import { GenerationLoader } from "./elements/loading-state";
import { createContext, useContext, type ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import { AssistantMarkdownText } from "./elements/markdown-text";
import { useSessionImageGallery } from "../ChatMessage/sessionImageGallery";
import { MessagePartRenderer } from "../ChatMessage/MessagePartRenderer";

type PlatformPartProps = ComponentProps<typeof MessagePartRenderer> & { partIndex: number };
const PlatformPartContext = createContext<PlatformPartProps | null>(null);

function MarkdownTextContent() {
  const presentation = useContext(PlatformPartContext);
  const messageId = useAuiState((state) => state.message.id);
  const role = useAuiState((state) => state.message.role);
  const status = useAuiState((state) => state.message.status?.type);
  const imageGallery = useSessionImageGallery();
  const isStreaming = status === "running";
  const smooth = useAuiState((state) => state.message.role === "assistant"
    && state.message.status?.type === "running"
    && !state.message.metadata.custom.isSynchronizing
    && !state.message.metadata.custom.cancelled);
  return (
    <AssistantMarkdownText
      headingAnchorContext={role === "assistant" ? {
        messageId,
        partIndex: presentation?.partIndex ?? 0,
      } : undefined}
      imageGallery={imageGallery}
      isStreaming={isStreaming}
      smooth={smooth && (presentation?.isLast ?? true)}
      defer
    />
  );
}

/** Native text/animation, with the platform's Markdown, link and preview policy. */
export function AssistantUiMarkdownText() {
  return <MessagePartPrimitive.Text component={MarkdownTextContent} smooth={false} />;
}

function PlatformPart() {
  const presentation = useContext(PlatformPartContext);
  return presentation ? <MessagePartRenderer {...presentation} /> : null;
}

const PART_COMPONENTS = {
  Text: AssistantUiMarkdownText,
  tools: { Override: PlatformPart },
  data: { Fallback: PlatformPart },
};

/** Business grouping chooses placement; the native part owns its content and status. */
export function AssistantUiMessagePart(props: PlatformPartProps) {
  return <PlatformPartContext.Provider value={props}>
    <MessagePrimitive.PartByIndex index={props.partIndex} components={PART_COMPONENTS} />
  </PlatformPartContext.Provider>;
}

export function AssistantUiMessageStatus() {
  const { t } = useTranslation();
  const synchronizing = useAuiState((state) => Boolean(state.message.metadata.custom.isSynchronizing));
  const hasText = useAuiState((state) => state.message.parts.some((part) => part.type === "text" && part.text.trim().length > 0));
  return <AuiIf condition={(state) => state.message.status?.type === "running" || Boolean(state.message.metadata.custom.isSynchronizing)}>
    <GenerationLoader data-assistant-ui-stream-status={synchronizing ? "synchronizing" : hasText ? "streaming" : "waiting"}
      label={t(synchronizing ? "chat.message.synchronizingResult" : hasText ? "chat.message.generating" : "chat.message.waitingForResponse")} />
  </AuiIf>;
}
