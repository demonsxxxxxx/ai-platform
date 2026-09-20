import type { MessagePart } from "../../../types";
import { groupPublicExecutionStepsForDisplay } from "../../../hooks/useAgent/publicStreamPresentation";

const ACTIONABLE_RUN_STATUS_PATTERN =
  /error|failed|failure|denied|blocked|forbidden|unauthori[sz]ed/i;

const WORK_ACTIVITY_TYPES: ReadonlySet<MessagePart["type"]> = new Set([
  "sandbox",
  "tool",
  "subagent",
  "execution_step",
  "execution_process",
  "todo",
  "summary",
]);

export function isWorkActivityPart(part: MessagePart): boolean {
  return WORK_ACTIVITY_TYPES.has(part.type);
}

export function isVisibleMessagePart(part: MessagePart): boolean {
  if (part.type === "thinking") {
    return false;
  }
  if (part.type !== "run_status") {
    return true;
  }

  return (
    part.severity === "warning" ||
    part.severity === "error" ||
    ACTIONABLE_RUN_STATUS_PATTERN.test(part.event_type)
  );
}

export function getVisibleMessageParts(parts: MessagePart[]): MessagePart[] {
  const visible = parts.flatMap((part): MessagePart[] => {
    if (!isVisibleMessagePart(part)) {
      return [];
    }
    if (part.type !== "subagent" || !part.parts?.length) {
      return [part];
    }

    return [{ ...part, parts: getVisibleMessageParts(part.parts) }];
  });
  return groupPublicExecutionStepsForDisplay(visible);
}
