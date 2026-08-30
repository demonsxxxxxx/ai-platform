import type { Message } from "../../../types";
import type { SessionConfig } from "../../../hooks/useAgent/types";
import type { ConnectionStatus } from "../../../types";

export type VisibleConnectionStatus = Exclude<ConnectionStatus, "connected">;

export function isSessionRunning(
  messages: Pick<Message, "isStreaming">[],
  isLoading: boolean,
): boolean {
  return isLoading || messages.some((message) => message.isStreaming);
}

export function shouldShowStreamingFooterSkeleton({
  connectionStatus,
  sessionRunning,
  messageCount,
  hasVisibleStreamingMessage,
}: {
  connectionStatus?: ConnectionStatus;
  sessionRunning: boolean;
  messageCount: number;
  hasVisibleStreamingMessage: boolean;
}): boolean {
  const lostStream =
    connectionStatus === "disconnected" ||
    connectionStatus === "reconnecting" ||
    connectionStatus === "recovering_gap";

  return (
    lostStream &&
    sessionRunning &&
    messageCount > 0 &&
    !hasVisibleStreamingMessage
  );
}

export function getVisibleConnectionStatus({
  connectionStatus,
  sessionId,
  currentRunId,
}: {
  connectionStatus?: ConnectionStatus;
  sessionId: string | null;
  currentRunId: string | null;
}): VisibleConnectionStatus | null {
  if (!sessionId || !currentRunId || connectionStatus === "connected") {
    return null;
  }
  return connectionStatus ?? null;
}

export function getRestoredModelSelection(
  config: Pick<SessionConfig, "agent_options">,
): {
  modelId: string;
  modelValue: string;
} {
  const modelId =
    typeof config.agent_options?.model_id === "string"
      ? config.agent_options.model_id
      : "";
  const modelValue =
    typeof config.agent_options?.model === "string"
      ? config.agent_options.model
      : "";

  return {
    modelId,
    modelValue,
  };
}
