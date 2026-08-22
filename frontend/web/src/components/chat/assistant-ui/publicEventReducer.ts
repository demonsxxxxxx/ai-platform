import type {
  ArtifactPart,
  Message,
  MessagePart,
  RunStatusPart,
  SubagentPart,
  TextPart,
  ThinkingPart,
  ToolPart,
} from "../../../types";
import type { V4PublicEvent } from "./publicEventAdapter";
import { isV4TerminalEvent } from "./publicEventAdapter";

export interface PublicV4ReducerBinding {
  sessionId: string;
  runId: string;
  generation: number;
  streamIncarnation: number;
}

export interface PublicV4ReducerState {
  messages: Message[];
  binding: PublicV4ReducerBinding;
  acceptedSequence: number | null;
  acceptedCursor: string | null;
  acceptedTransportCursor: string | null;
  seenEventIds: ReadonlySet<string>;
  seenSemanticKeys: ReadonlySet<string>;
  terminal: boolean;
}

export interface PublicV4Reduction {
  state: PublicV4ReducerState;
  accepted: boolean;
  semanticApplied: boolean;
}

function cloneSet(values: ReadonlySet<string>, addition?: string): ReadonlySet<string> {
  const result = new Set(values);
  if (addition) result.add(addition);
  return result;
}

function messageIndex(messages: Message[], id: string): number {
  return messages.findIndex((message) => message.id === id);
}

function ensureAssistant(messages: Message[], event: V4PublicEvent): { messages: Message[]; index: number } {
  const id = event.messageId;
  if (!id) return { messages, index: -1 };
  const existing = messageIndex(messages, id);
  if (existing >= 0) return { messages, index: existing };
  const next = [...messages, {
    id,
    role: "assistant" as const,
    content: "",
    timestamp: new Date(event.emittedAt),
    parts: [],
    isStreaming: true,
    runId: event.runId,
  }];
  return { messages: next, index: next.length - 1 };
}

function updateMessage(messages: Message[], index: number, update: (message: Message) => Message): Message[] {
  if (index < 0 || index >= messages.length) return messages;
  const next = [...messages];
  next[index] = update(messages[index]);
  return next;
}

function upsertPart(parts: MessagePart[], predicate: (part: MessagePart) => boolean, nextPart: MessagePart): MessagePart[] {
  const index = parts.findIndex(predicate);
  if (index < 0) return [...parts, nextPart];
  const next = [...parts];
  next[index] = nextPart;
  return next;
}

function replaceTextPart(parts: MessagePart[], content: string): MessagePart[] {
  const text: TextPart = { type: "text", content };
  const index = parts.findIndex((part) => part.type === "text");
  if (index < 0) return [...parts, text];
  const next = [...parts];
  next[index] = text;
  return next;
}

function statusPart(event: V4PublicEvent, message: string, severity: RunStatusPart["severity"]): RunStatusPart {
  return {
    type: "run_status",
    event_id: event.eventId,
    event_type: event.eventType,
    stage: "public-run",
    message,
    severity,
    run_reference: event.runId,
    sequence: event.sequence ?? undefined,
    created_at: event.emittedAt,
  };
}

function stringValue(payload: Record<string, unknown>, key: string): string {
  return typeof payload[key] === "string" ? payload[key] : "";
}

function numberValue(payload: Record<string, unknown>, key: string): number {
  return typeof payload[key] === "number" ? payload[key] : 0;
}

function payloadOf(event: V4PublicEvent): Record<string, unknown> {
  return (event.event as unknown as { payload: Record<string, unknown> }).payload;
}

function applyApplicationEvent(messages: Message[], event: V4PublicEvent): Message[] {
  const payload = payloadOf(event);
  const ensured = ensureAssistant(messages, event);
  const next = ensured.messages;
  const index = ensured.index;
  if (event.eventType === "stream.open" || event.eventType === "stream.heartbeat" || event.eventType === "stream.gap" || event.eventType === "stream.end") return messages;
  if (index < 0) return messages;

  switch (event.eventType) {
    case "message.started":
      return updateMessage(next, index, (message) => ({ ...message, isStreaming: true }));
    case "message.delta": {
      const delta = stringValue(payload, "delta");
      return updateMessage(next, index, (message) => {
        const content = message.content + delta;
        return { ...message, content, isStreaming: true, parts: replaceTextPart(message.parts || [], content) };
      });
    }
    case "message.completed": {
      const content = stringValue(payload, "content");
      return updateMessage(next, index, (message) => ({
        ...message,
        content,
        isStreaming: false,
        parts: replaceTextPart(message.parts || [], content),
      }));
    }
    case "thinking.started": {
      const thinking: ThinkingPart = { type: "thinking", content: "", thinking_id: event.messageId || event.eventId, isStreaming: true };
      return updateMessage(next, index, (message) => ({ ...message, parts: upsertPart(message.parts || [], (part) => part.type === "thinking", thinking) }));
    }
    case "thinking.completed": {
      const thinking: ThinkingPart = { type: "thinking", content: "", thinking_id: event.messageId || event.eventId, isStreaming: false };
      return updateMessage(next, index, (message) => ({ ...message, parts: upsertPart(message.parts || [], (part) => part.type === "thinking", thinking) }));
    }
    case "model.completed":
      return updateMessage(next, index, (message) => ({ ...message, parts: [...(message.parts || []), statusPart(event, "Model completed", "info")] }));
    case "tool.started": {
      const id = stringValue(payload, "operation_id");
      const tool: ToolPart = { type: "tool", id, name: stringValue(payload, "display_name"), args: {}, isPending: true };
      return updateMessage(next, index, (message) => ({ ...message, parts: upsertPart(message.parts || [], (part) => part.type === "tool" && part.id === id, tool) }));
    }
    case "tool.completed":
    case "tool.failed":
    case "tool.denied": {
      const id = stringValue(payload, "operation_id");
      const failed = event.eventType !== "tool.completed";
      const tool: ToolPart = {
        type: "tool",
        id,
        name: stringValue(payload, "display_name"),
        args: {},
        isPending: false,
        success: !failed,
        error: failed ? stringValue(payload, event.eventType === "tool.denied" ? "denial_code" : "failure_category") : undefined,
      };
      return updateMessage(next, index, (message) => ({ ...message, parts: upsertPart(message.parts || [], (part) => part.type === "tool" && part.id === id, tool) }));
    }
    case "subagent.started": {
      const id = stringValue(payload, "subagent_id");
      const subagent: SubagentPart = { type: "subagent", agent_id: id, agent_name: stringValue(payload, "display_name"), input: "", depth: 0, isPending: true, status: "running" };
      return updateMessage(next, index, (message) => ({ ...message, parts: upsertPart(message.parts || [], (part) => part.type === "subagent" && part.agent_id === id, subagent) }));
    }
    case "subagent.progress": {
      const id = stringValue(payload, "subagent_id");
      return updateMessage(next, index, (message) => ({ ...message, parts: (message.parts || []).map((part) => part.type === "subagent" && part.agent_id === id ? { ...part, isPending: true, status: "running" } : part) }));
    }
    case "subagent.completed":
    case "subagent.failed":
    case "subagent.cancelled": {
      const id = stringValue(payload, "subagent_id");
      const status: SubagentPart["status"] = event.eventType === "subagent.completed" ? "complete" : event.eventType === "subagent.cancelled" ? "cancelled" : "error";
      return updateMessage(next, index, (message) => ({ ...message, parts: (message.parts || []).map((part) => part.type === "subagent" && part.agent_id === id ? { ...part, isPending: false, status, success: status === "complete", cancelled: status === "cancelled", error: status === "error" ? stringValue(payload, "failure_category") : undefined } : part) }));
    }
    case "artifact.created":
    case "artifact.ready":
    case "artifact.failed": {
      const artifactId = stringValue(payload, "artifact_id");
      const artifact: ArtifactPart = {
        type: "artifact",
        artifact_id: artifactId,
        artifact_type: stringValue(payload, "media_type") || "application/octet-stream",
        label: stringValue(payload, "filename") || artifactId,
        content_type: stringValue(payload, "media_type") || "application/octet-stream",
        size_bytes: numberValue(payload, "size_bytes"),
        status: stringValue(payload, "status"),
        created_at: event.emittedAt,
      };
      return updateMessage(next, index, (message) => ({ ...message, parts: upsertPart(message.parts || [], (part) => part.type === "artifact" && part.artifact_id === artifactId, artifact) }));
    }
    case "policy.checking":
      return updateMessage(next, index, (message) => ({ ...message, parts: [...(message.parts || []), statusPart(event, "Policy checking", "info")] }));
    case "policy.allowed":
      return updateMessage(next, index, (message) => ({ ...message, parts: [...(message.parts || []), statusPart(event, "Policy allowed", "info")] }));
    case "policy.denied":
      return updateMessage(next, index, (message) => ({ ...message, parts: [...(message.parts || []), statusPart(event, "Policy denied", "warning")] }));
    case "run.cancel_requested":
      return updateMessage(next, index, (message) => ({ ...message, parts: [...(message.parts || []), statusPart(event, "Cancellation requested", "warning")] }));
    case "run.succeeded":
      return updateMessage(next, index, (message) => ({ ...message, isStreaming: false, parts: [...(message.parts || []), statusPart(event, "Run succeeded", "info")] }));
    case "run.cancelled":
      return updateMessage(next, index, (message) => ({ ...message, isStreaming: false, cancelled: true, parts: [...(message.parts || []), { type: "cancelled" }, statusPart(event, "Run cancelled", "warning")] }));
    case "run.failed":
      return updateMessage(next, index, (message) => ({ ...message, isStreaming: false, parts: [...(message.parts || []), statusPart(event, stringValue(payload, "default_message"), "error")] }));
    default:
      return messages;
  }
}

export function createPublicV4ReducerState(
  messages: Message[],
  binding: PublicV4ReducerBinding,
): PublicV4ReducerState {
  return {
    messages,
    binding,
    acceptedSequence: null,
    acceptedCursor: null,
    acceptedTransportCursor: null,
    seenEventIds: new Set(),
    seenSemanticKeys: new Set(),
    terminal: false,
  };
}

export function reducePublicV4Event(
  state: PublicV4ReducerState,
  event: V4PublicEvent,
): PublicV4Reduction {
  if (event.runId !== state.binding.runId || event.streamIncarnation !== state.binding.streamIncarnation || (event.generation != null && event.generation !== state.binding.generation)) {
    return { state, accepted: false, semanticApplied: false };
  }
  if (state.seenEventIds.has(event.eventId)) {
    return { state, accepted: true, semanticApplied: false };
  }
  const semanticApplied = !state.seenSemanticKeys.has(event.semanticKey);
  if (event.sequence !== null && state.acceptedSequence !== null && event.sequence <= state.acceptedSequence && semanticApplied) {
    return { state, accepted: false, semanticApplied: false };
  }
  const messages = semanticApplied ? applyApplicationEvent(state.messages, event) : state.messages;
  const next: PublicV4ReducerState = {
    ...state,
    messages,
    acceptedSequence: event.sequence === null
      ? state.acceptedSequence
      : state.acceptedSequence === null
        ? event.sequence
        : Math.max(state.acceptedSequence, event.sequence),
    acceptedCursor: event.eventId,
    acceptedTransportCursor: event.transportCursor,
    seenEventIds: cloneSet(state.seenEventIds, event.eventId),
    seenSemanticKeys: cloneSet(state.seenSemanticKeys, event.semanticKey),
    terminal: state.terminal || isV4TerminalEvent(event),
  };
  return { state: next, accepted: true, semanticApplied };
}
