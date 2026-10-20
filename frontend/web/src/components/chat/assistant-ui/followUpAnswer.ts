import { getPublicTerminalPresentationDefinition } from "../../../hooks/useAgent/publicTerminalPresentation";
import type { Message } from "../../../types";

/**
 * A follow-up answer is the last assistant message whose Run settled successfully.
 * Terminal facts come from the one public terminal owner, and result-unavailable
 * answers have no displayable reply to follow up on.
 */
export function settledFollowUpAnswer(
  messages: readonly Message[],
): Message | null {
  const last = messages[messages.length - 1];
  if (!last || last.role !== "assistant" || !last.runId) return null;
  if (last.isStreaming || last.isSynchronizing || last.cancelled) return null;
  const terminal = last.parts?.some(
    (part) =>
      part.type === "run_status" &&
      (getPublicTerminalPresentationDefinition(part.event_type) !== undefined ||
        part.event_type === "terminal_result_unavailable"),
  );
  return terminal ? null : last;
}

/** Account, session and answer identity: any change owns a different suggestion set. */
export function followUpOwnerKey(
  authScopeKey: string,
  sessionId: string | null,
  answer: Message | null,
): string | null {
  if (!authScopeKey || !sessionId || !answer?.runId) return null;
  return `${authScopeKey}\u0000${sessionId}\u0000${answer.runId}`;
}
