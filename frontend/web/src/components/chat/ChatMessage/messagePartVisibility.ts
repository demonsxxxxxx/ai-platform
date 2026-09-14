import type { MessagePart } from "../../../types";
import { groupPublicExecutionStepsForDisplay } from "../../../hooks/useAgent/publicStreamPresentation";

const ACTIONABLE_RUN_STATUS_PATTERN =
  /error|failed|failure|denied|blocked|forbidden|unauthori[sz]ed/i;

export function isVisibleMessagePart(part: MessagePart): boolean {
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
