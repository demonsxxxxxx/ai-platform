import { useMemo } from "react";
import {
  useExternalStoreRuntime,
  type AppendMessage,
  type AttachmentAdapter,
  type ExternalStoreAdapter,
  type ExternalStoreThreadListAdapter,
  type ThreadMessageLike,
  type ThreadSuggestion,
} from "@assistant-ui/react";
import { getPublicTerminalPresentationDefinition } from "../../../hooks/useAgent/publicTerminalPresentation";
import type { Message, MessagePart } from "../../../types";
import {
  assistantTextPartRole,
  composeAssistantPreviewText,
  hasMarkedAssistantTextParts,
} from "../../../types/assistantTextParts";
import {
  getVisibleMessageParts,
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
      if (role === null) return null;
      if (role === "work") return { type: "data-work", data: {} };
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
        status: part.cancelled ? "cancelled" : part.status,
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
      // Business renderers keep authorized files, timelines and safe error copy outside generic data UIs.
      return { type: "data-platform", data: {} };
  }
}

export function getAssistantUiDisplayParts(message: Message): MessagePart[] {
  return getVisibleMessageParts(message.parts || []).filter(
    (part) => part.type !== "text" || assistantTextPartRole(part) !== null,
  );
}

function messageStatus(message: Message): ThreadMessageLike["status"] {
  const terminal = message.parts?.findLast((part) => part.type === "run_status" && (
    getPublicTerminalPresentationDefinition(part.event_type) || part.event_type === "terminal_result_unavailable"
  ));
  const cancelled = message.cancelled || message.parts?.some((part) => part.type === "cancelled") ||
    (terminal?.type === "run_status" && getPublicTerminalPresentationDefinition(terminal.event_type)?.detailKind === "cancelled");
  if (cancelled) {
    return { type: "incomplete", reason: "cancelled" };
  }
  if (terminal) return { type: "incomplete", reason: "error" };
  // Synchronization is a content-read state after the Run is already terminal.
  if (message.isStreaming && !message.isSynchronizing) return { type: "running" };
  return { type: "complete", reason: "stop" };
}

export function toAssistantUiMessage(message: Message): ThreadMessageLike {
  let lastPublicPartId: string | null = null;
  const content = (message.role === "user" ? [] : getAssistantUiDisplayParts(message)).flatMap((part) => {
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
    content: message.role === "user" ? message.content : content.length ? content : fallbackContent,
    createdAt: message.timestamp,
    ...(message.role === "assistant"
      ? {
          status: messageStatus(message),
        }
      : {}),
    metadata: {
      custom: {
        runId: message.runId,
        cancelled: message.cancelled,
        isSynchronizing: message.isSynchronizing,
      },
    },
  };
}

export function useAssistantUiExternalStoreRuntime(
  messages: readonly Message[],
  isRunning: boolean,
  actions: AssistantUiRuntimeActions,
  gates: { disabled: boolean; sendDisabled: boolean } = { disabled: false, sendDisabled: false },
  threadList?: ExternalStoreThreadListAdapter,
  attachments?: AttachmentAdapter,
  suggestions: readonly ThreadSuggestion[] = [],
) {
  const adapter = useMemo<ExternalStoreAdapter<Message>>(
    () => ({
      messages,
      isRunning,
      isDisabled: gates.disabled,
      isSendDisabled: gates.sendDisabled,
      suggestions,
      adapters: { threadList, attachments },
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
    }),
    [actions, attachments, gates.disabled, gates.sendDisabled, isRunning, messages, suggestions, threadList],
  );
  return useExternalStoreRuntime(adapter);
}

export { appendContent };
