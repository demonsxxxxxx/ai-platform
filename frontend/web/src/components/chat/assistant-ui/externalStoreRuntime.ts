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

function definedData(values: Record<string, unknown>): Record<string, unknown> | undefined {
  const data = Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined));
  return Object.keys(data).length ? data : undefined;
}

type AssistantUiContentPart = Exclude<ThreadMessageLike["content"], string>[number];

function convertPart(part: MessagePart, index: number): AssistantUiContentPart | null {
  switch (part.type) {
    case "text":
      return { type: "text", text: part.content };
    case "thinking":
      return { type: "reasoning", text: "", status: part.isStreaming ? { type: "running" } : { type: "complete" } };
    case "tool": {
      const data = definedData({
        inputSummary: part.public_operation_id
          ? part.public_input_summary
          : undefined,
        resultSummary:
          part.public_operation_id && typeof part.result === "string"
            ? part.result
            : undefined,
        durationMs: part.duration_ms,
        evidenceRefs: part.evidence_refs,
        artifactRefs: part.artifact_refs,
        eventId: part.event_id,
        causationEventId: part.causation_event_id,
      });
      return {
        type: "tool-call",
        toolCallId: part.public_operation_id || `tool-${index}`,
        toolName: part.public_operation_id ? part.name : "Tool",
        args: part.public_operation_id && part.public_category
          ? {
              category: part.public_category,
              ...(part.public_input_summary
                ? { summary: part.public_input_summary }
                : {}),
            }
          : {},
        argsText:
          part.public_operation_id && part.public_input_summary
            ? part.public_input_summary
            : "",
        isError: part.success === false,
        ...(data ? { data } : {}),
      } as AssistantUiContentPart;
    }
    case "subagent": {
      const data = definedData({
        id: part.agent_id,
        operationId: part.public_operation_id && part.public_operation_id !== part.agent_id
          ? part.public_operation_id
          : undefined,
        name: part.agent_name,
        parentId: part.parent_agent_id,
        causationEventId: part.causation_event_id,
        status: part.status,
        depth: part.depth,
        durationMs: part.duration_ms,
        progressPercent: part.progress_percent,
        currentCategory: part.current_category,
        eventId: part.event_id,
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
  const content = (message.parts || [])
    .map((part, index) => convertPart(part, index))
    .filter((part): part is AssistantUiContentPart => part !== null);
  return {
    id: message.id,
    role: message.role,
    content: content.length ? content : message.content,
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
