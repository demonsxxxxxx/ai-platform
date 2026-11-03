import { AuiIf, MessagePartPrimitive, MessagePrimitive, useAuiState } from "@assistant-ui/react";
import { GenerationLoader } from "./elements/loading-state";
import { createContext, useContext, useEffect, useState, type ComponentProps } from "react";
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
  const stopped = useAuiState((state) => state.message.role === "assistant" && Boolean(state.message.metadata.custom.cancelled));
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
      streamingCaret={smooth && (presentation?.isLast ?? true)}
      stoppedCaret={stopped}
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

function formatElapsed(t: (key: string, options: Record<string, number>) => string, totalSeconds: number) {
  if (totalSeconds < 60) return t("chat.message.elapsedSeconds", { seconds: totalSeconds });
  return t("chat.message.elapsedMinutes", { minutes: Math.floor(totalSeconds / 60), seconds: totalSeconds % 60 });
}

/** Seconds since `startedAt`, refreshed once a second while mounted. */
function useElapsedSeconds(startedAt?: Date): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!startedAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);
  if (!startedAt) return null;
  return Math.max(0, Math.floor((now - startedAt.getTime()) / 1000));
}

export function AssistantUiMessageStatus({ activityLabel, startedAt }: {
  /** Public label of the step running now, if any. */
  activityLabel?: string | null;
  /** When the turn started; drives the elapsed counter. */
  startedAt?: Date;
} = {}) {
  const { t } = useTranslation();
  const elapsedSeconds = useElapsedSeconds(startedAt);
  const synchronizing = useAuiState((state) => Boolean(state.message.metadata.custom.isSynchronizing));
  const hasText = useAuiState((state) => state.message.parts.some((part) => part.type === "text" && part.text.trim().length > 0));
  // While text streams, the caret after the text is the visible cue; the
  // status stays announced for assistive technology without a second row.
  return <AuiIf condition={(state) => state.message.status?.type === "running" || Boolean(state.message.metadata.custom.isSynchronizing)}>
    <GenerationLoader data-assistant-ui-stream-status={synchronizing ? "synchronizing" : hasText ? "streaming" : "waiting"}
      visuallyHidden={hasText && !synchronizing}
      elapsed={!hasText && !synchronizing && elapsedSeconds !== null && elapsedSeconds > 0 ? formatElapsed(t, elapsedSeconds) : undefined}
      label={synchronizing ? t("chat.message.synchronizingResult")
        : hasText ? t("chat.message.generating")
          : activityLabel ? t("chat.message.currentActivity", { action: activityLabel })
            : t("chat.message.waitingForResponse")} />
  </AuiIf>;
}
