import { useMemo } from "react";
import {
  useExternalStoreRuntime,
  type AppendMessage,
  type ExternalStoreAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import type { Message, MessagePart } from "../../../types";

export interface AssistantUiRuntimeActions {
  sendMessage: (content: string) => Promise<unknown>;
  cancel: () => Promise<unknown>;
  reconnect: () => Promise<unknown>;
  loadHistory: () => Promise<unknown>;
}

function appendContent(message: AppendMessage): string {
  if (typeof message.content === "string") return message.content;
  return message.content
    .filter((part): part is { type: "text"; text: string } => part.type === "text")
    .map((part) => part.text)
    .join("");
}

type AssistantUiContentPart = Exclude<ThreadMessageLike["content"], string>[number];

function convertPart(part: MessagePart): AssistantUiContentPart | null {
  switch (part.type) {
    case "text":
      return { type: "text", text: part.content };
    case "thinking":
      return { type: "reasoning", text: "", status: part.isStreaming ? { type: "running" } : { type: "complete" } };
    case "tool":
      return {
        type: "tool-call",
        toolCallId: "tool",
        toolName: "Tool",
        args: {},
        argsText: "",
        isError: part.success === false,
      };
    case "artifact":
      // Artifact authorization and rendering stay in ChatMessage's renderer.
      return { type: "data-artifact", data: { label: part.label, status: part.status } };
    default:
      return null;
  }
}

export function toAssistantUiMessage(message: Message): ThreadMessageLike {
  const content = (message.parts || [])
    .map(convertPart)
    .filter((part): part is AssistantUiContentPart => part !== null);
  return {
    id: message.id,
    role: message.role,
    content: content.length ? content : message.content,
    createdAt: message.timestamp,
    status: message.isStreaming ? { type: "running" } : { type: "complete", reason: "stop" },
    metadata: {
      custom: {
        runId: message.runId,
        cancelled: message.cancelled,
      },
    },
  };
}

export function useAssistantUiExternalStoreRuntime(
  messages: readonly Message[],
  isRunning: boolean,
  actions: AssistantUiRuntimeActions,
) {
  const adapter = useMemo<ExternalStoreAdapter<Message>>(
    () => ({
      messages,
      isRunning,
      isSendDisabled: false,
      convertMessage: toAssistantUiMessage,
      onNew: async (message) => {
        await actions.sendMessage(appendContent(message));
      },
      onCancel: async () => {
        await actions.cancel();
      },
      onRefetchThread: async () => {
        await actions.loadHistory();
      },
      onReload: async () => {
        await actions.reconnect();
      },
    }),
    [actions, isRunning, messages],
  );
  return useExternalStoreRuntime(adapter);
}

export { appendContent };
