import type { AssistantTextRole, MessagePart, TextPart } from "./message";

export const ASSISTANT_TEXT_PART_SCHEMA_VERSION =
  "ai-platform.assistant-text-part.v1" as const;
export const MAX_ASSISTANT_TEXT_PART_DELTA_CODE_POINTS = 8192;

const SAFE_REF_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$/;
const ASSISTANT_TEXT_PART_EVENT_TYPES = new Set([
  "message.part.delta",
  "message.part.classified",
]);

export type AssistantTextPartEventType =
  | "message.part.delta"
  | "message.part.classified";

export function isAssistantTextPartEventType(
  eventType: string,
): eventType is AssistantTextPartEventType {
  return ASSISTANT_TEXT_PART_EVENT_TYPES.has(eventType);
}

export function isSafeAssistantTextPartId(value: unknown): value is string {
  return typeof value === "string" && SAFE_REF_PATTERN.test(value);
}

function codePointLength(value: string): number {
  let length = 0;
  const characters = value[Symbol.iterator]();
  while (!characters.next().done) length += 1;
  return length;
}

export function isValidAssistantTextPartDelta(value: unknown): value is string {
  return (
    typeof value === "string" &&
    codePointLength(value) > 0 &&
    codePointLength(value) <= MAX_ASSISTANT_TEXT_PART_DELTA_CODE_POINTS
  );
}

export function isValidAssistantTextPartPayload(
  eventType: string,
  payload: unknown,
): payload is Record<string, unknown> {
  if (
    !isAssistantTextPartEventType(eventType) ||
    !payload ||
    typeof payload !== "object" ||
    Array.isArray(payload)
  ) {
    return false;
  }
  const record = payload as Record<string, unknown>;
  if (
    record.schema_version !== ASSISTANT_TEXT_PART_SCHEMA_VERSION ||
    !isSafeAssistantTextPartId(record.part_id)
  ) {
    return false;
  }
  if (eventType === "message.part.delta") {
    return (
      Object.keys(record).length === 3 &&
      Object.hasOwn(record, "delta") &&
      Object.hasOwn(record, "schema_version") &&
      Object.hasOwn(record, "part_id") &&
      isValidAssistantTextPartDelta(record.delta)
    );
  }
  return (
    Object.keys(record).length === 3 &&
    Object.hasOwn(record, "role") &&
    Object.hasOwn(record, "schema_version") &&
    Object.hasOwn(record, "part_id") &&
    (record.role === "answer" || record.role === "work")
  );
}

function isAssistantTextRole(value: unknown): value is AssistantTextRole {
  return value === "pending" || value === "answer" || value === "work";
}

export function isMarkedAssistantTextPart(part: MessagePart): boolean {
  return (
    part.type === "text" &&
    (part.public_part_id !== undefined || part.text_role !== undefined)
  );
}

export function isValidAssistantTextPart(part: TextPart): boolean {
  return (
    isSafeAssistantTextPartId(part.public_part_id) &&
    isAssistantTextRole(part.text_role) &&
    part.logical_id === part.public_part_id &&
    (part.depth === undefined || part.depth === 0)
  );
}

/** Legacy unmarked text remains public; malformed versioned parts fail closed. */
export function assistantTextPartRole(
  part: TextPart,
): AssistantTextRole | "legacy" | null {
  if (part.public_part_id === undefined && part.text_role === undefined) {
    return "legacy";
  }
  return isValidAssistantTextPart(part) ? part.text_role! : null;
}

export function isAssistantTextPartPreviewVisible(part: TextPart): boolean {
  const role = assistantTextPartRole(part);
  return role === "legacy" || role === "pending" || role === "answer";
}

export function isAssistantTextPartAnswer(part: TextPart): boolean {
  const role = assistantTextPartRole(part);
  return role === "legacy" || role === "answer";
}

export function hasMarkedAssistantTextParts(
  parts: readonly MessagePart[] | undefined,
): boolean {
  return Boolean(parts?.some(isMarkedAssistantTextPart));
}

export function selectAssistantPreviewTextParts(
  parts: readonly MessagePart[] | undefined,
): TextPart[] {
  return (parts || []).filter(
    (part): part is TextPart =>
      part.type === "text" && isAssistantTextPartPreviewVisible(part),
  );
}

export function selectAssistantAnswerTextParts(
  parts: readonly MessagePart[] | undefined,
): TextPart[] {
  return (parts || []).filter(
    (part): part is TextPart =>
      part.type === "text" && isAssistantTextPartAnswer(part),
  );
}

function composeTextPartGroups(parts: readonly TextPart[]): string[] {
  const groups: Array<{ publicPartId: string | null; content: string }> = [];
  for (const part of parts) {
    const role = assistantTextPartRole(part);
    if (role === null || role === "work" || !part.content) continue;
    const publicPartId = role === "legacy" ? null : part.public_part_id!;
    const last = groups[groups.length - 1];
    if (last && last.publicPartId === publicPartId) {
      last.content += part.content;
    } else {
      groups.push({ publicPartId, content: part.content });
    }
  }
  return groups.map((group) => group.content);
}

/** Preview projection: legacy chunks concatenate; distinct public parts use a blank line. */
export function composeAssistantPreviewText(
  parts: readonly MessagePart[] | undefined,
): string {
  const selected = selectAssistantPreviewTextParts(parts);
  const groups = composeTextPartGroups(selected);
  return hasMarkedAssistantTextParts(parts)
    ? groups.join("\n\n")
    : groups.join("");
}

/** Copy projection: legacy text is retained and versioned text must be an answer. */
export function selectAssistantCopyText(
  parts: readonly MessagePart[] | undefined,
  fallbackContent = "",
): string {
  const textParts = (parts || []).filter(
    (part): part is TextPart => part.type === "text",
  );
  if (!hasMarkedAssistantTextParts(parts)) {
    return textParts.length
      ? textParts.map((part) => part.content).join("\n")
      : fallbackContent;
  }
  return composeTextPartGroups(
    selectAssistantAnswerTextParts(parts),
  ).join("\n\n");
}

export interface AssistantTextPartReduction {
  accepted: boolean;
  parts: MessagePart[];
  content: string;
}

function messageHasStarted(parts: readonly MessagePart[]): boolean {
  return parts.some(
    (part) =>
      part.type === "run_status" &&
      part.event_type === "public_activity" &&
      part.stage === "message_started",
  );
}

export function messageTextPartsAreClosed(
  parts: readonly MessagePart[],
): boolean {
  return parts.some(
    (part) =>
      part.type === "run_status" &&
      part.event_type === "public_activity" &&
      part.stage === "message_completed",
  );
}

function allPublicPartOccurrences(
  parts: readonly MessagePart[],
  partId: string,
): TextPart[] {
  const occurrences: TextPart[] = [];
  const visit = (items: readonly MessagePart[]) => {
    for (const part of items) {
      if (part.type === "text" && part.public_part_id === partId) {
        occurrences.push(part);
      } else if (part.type === "subagent" && part.parts) {
        visit(part.parts);
      }
    }
  };
  visit(parts);
  return occurrences;
}

/** Apply a closed v1 text-source event to its exact message-local part. */
export function reduceAssistantTextPartEvent(
  eventType: string,
  data: Record<string, unknown>,
  parts: MessagePart[],
): AssistantTextPartReduction {
  const rejected = (): AssistantTextPartReduction => ({
    accepted: false,
    parts,
    content: composeAssistantPreviewText(parts),
  });
  if (
    !isAssistantTextPartEventType(eventType) ||
    data.schema_version !== ASSISTANT_TEXT_PART_SCHEMA_VERSION ||
    !isSafeAssistantTextPartId(data.part_id) ||
    !messageHasStarted(parts) ||
    messageTextPartsAreClosed(parts)
  ) {
    return rejected();
  }

  const partId = data.part_id;
  const occurrences = allPublicPartOccurrences(parts, partId);
  const rootIndexes = parts.flatMap((part, index) =>
    part.type === "text" && part.public_part_id === partId ? [index] : [],
  );
  if (occurrences.length !== rootIndexes.length || rootIndexes.length > 1) {
    return rejected();
  }

  if (eventType === "message.part.delta") {
    if (
      !isValidAssistantTextPartDelta(data.delta) ||
      parts.some(
        (part) => part.type === "text" && !isMarkedAssistantTextPart(part),
      )
    ) {
      return rejected();
    }
    if (rootIndexes.length === 0) {
      const nextParts: MessagePart[] = [
        ...parts,
        {
          type: "text",
          content: data.delta,
          logical_id: partId,
          public_part_id: partId,
          text_role: "pending",
        },
      ];
      return {
        accepted: true,
        parts: nextParts,
        content: composeAssistantPreviewText(nextParts),
      };
    }
    const index = rootIndexes[0];
    const existing = parts[index];
    if (existing?.type !== "text" || !isValidAssistantTextPart(existing)) {
      return rejected();
    }
    const nextParts = [...parts];
    nextParts[index] = { ...existing, content: existing.content + data.delta };
    return {
      accepted: true,
      parts: nextParts,
      content: composeAssistantPreviewText(nextParts),
    };
  }

  if (
    rootIndexes.length !== 1 ||
    (data.role !== "answer" && data.role !== "work")
  ) {
    return rejected();
  }
  const index = rootIndexes[0];
  const existing = parts[index];
  if (existing?.type !== "text" || !isValidAssistantTextPart(existing)) {
    return rejected();
  }
  if (existing.text_role === data.role) {
    return { accepted: true, parts, content: composeAssistantPreviewText(parts) };
  }
  // Classification is monotonic: an answer may later be recognized as work,
  // while already-classified work can never become answer text.
  if (
    existing.text_role === "work" ||
    (existing.text_role !== "pending" && existing.text_role !== "answer")
  ) {
    return rejected();
  }
  const nextParts = [...parts];
  nextParts[index] = { ...existing, text_role: data.role };
  return {
    accepted: true,
    parts: nextParts,
    content: composeAssistantPreviewText(nextParts),
  };
}

export function assistantTextPartIdIsPresentInOtherMessage(
  messages: readonly { id: string; parts?: MessagePart[] }[],
  currentMessageId: string,
  partId: string,
): boolean {
  return messages.some(
    (message) =>
      message.id !== currentMessageId &&
      allPublicPartOccurrences(message.parts || [], partId).length > 0,
  );
}
