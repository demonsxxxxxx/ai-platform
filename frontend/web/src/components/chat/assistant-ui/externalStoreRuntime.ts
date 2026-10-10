import { useMemo } from "react";
import {
  useExternalStoreRuntime,
  type AppendMessage,
  type ExternalStoreAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import type { Message, MessagePart } from "../../../types";
import {
  assistantTextPartRole,
  composeAssistantPreviewText,
  hasMarkedAssistantTextParts,
} from "../../../types/assistantTextParts";
import {
  getPublicToolDisplayName,
  isPublicSubagentPart,
  isPublicToolPresentation,
  PUBLIC_SUBAGENT_DISPLAY_NAME,
} from "../ChatMessage/messagePartVisibility";

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

function definedData(values: Record<string, unknown>): Record<string, unknown> | undefined {
  const data = Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined));
  return Object.keys(data).length ? data : undefined;
}

type AssistantUiContentPart = Exclude<ThreadMessageLike["content"], string>[number];

function convertPart(
  part: MessagePart,
  textPrefix = "",
): AssistantUiContentPart | null {
  switch (part.type) {
    case "text": {
      const role = assistantTextPartRole(part);
      if (role === null || role === "work") return null;
      return { type: "text", text: `${textPrefix}${part.content}` };
    }
    case "thinking":
      return null;
    case "tool": {
      const toolName = getPublicToolDisplayName(
        part.public_category,
        part.public_display_name,
      );
      if (
        !toolName ||
        !isPublicToolPresentation(
          part.public_operation_id,
          part.public_category,
          part.status,
          part.duration_ms,
        )
      ) {
        return null;
      }
      const data = definedData({
        category: part.public_category,
        durationMs: part.duration_ms,
      });
      return {
        type: "tool-call",
        toolCallId: part.public_operation_id,
        toolName,
        args: {},
        argsText: "",
        isError: part.status === "failed" || part.status === "denied",
        ...(data ? { data } : {}),
      } as AssistantUiContentPart;
    }
    case "subagent": {
      if (!isPublicSubagentPart(part)) {
        return null;
      }
      const data = definedData({
        id: part.public_operation_id,
        name: PUBLIC_SUBAGENT_DISPLAY_NAME,
        parentId: part.parent_agent_id,
        status: part.status,
        depth: part.depth,
        durationMs: part.duration_ms,
        progressPercent: part.progress_percent,
        currentCategory: part.current_category,
      });
      return { type: "data-subagent", data } as AssistantUiContentPart;
    }
    case "artifact":
      // Artifact authorization and rendering stay in ChatMessage's renderer.
      return { type: "data-artifact", data: { label: part.label, status: part.status } };
    default:
      return null;
  }
}

export function toAssistantUiMessage(message: Message): ThreadMessageLike {
  let lastPublicPartId: string | null = null;
  const content = (message.parts || []).flatMap((part) => {
    let prefix = "";
    if (part.type === "text") {
      const role = assistantTextPartRole(part);
      if (role !== "legacy" && role !== null && role !== "work") {
        prefix =
          lastPublicPartId !== null && lastPublicPartId !== part.public_part_id
            ? "\n\n"
            : "";
        lastPublicPartId = part.public_part_id || null;
      }
    }
    const converted = convertPart(part, prefix);
    return converted ? [converted] : [];
  });
  const fallbackContent = hasMarkedAssistantTextParts(message.parts)
    ? composeAssistantPreviewText(message.parts)
    : message.content;
  return {
    id: message.id,
    role: message.role,
    content: content.length ? content : fallbackContent,
    createdAt: message.timestamp,
    ...(message.role === "assistant"
      ? {
          status: message.isStreaming
            ? { type: "running" as const }
            : { type: "complete" as const, reason: "stop" as const },
        }
      : {}),
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
