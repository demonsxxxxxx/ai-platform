import type { ToolPermissionDecision, ToolPermissionPart } from "../../../types";

export interface ToolPermissionCardState {
  status: ToolPermissionPart["status"];
  decision: ToolPermissionDecision | undefined;
  error: string | null;
}

export interface OrdinaryUserToolPermissionPresentation {
  titleKey: string;
  messageKey: string;
}

/**
 * Convert governed tool access into a non-interactive product state.
 * The returned keys intentionally retain the recorded decision without exposing
 * an approval control to ordinary chat users.
 */
export function getOrdinaryUserToolPermissionPresentation(
  part: ToolPermissionPart,
): OrdinaryUserToolPermissionPresentation {
  if (part.status === "pending") {
    return {
      titleKey: "chat.toolPermission.pending.title",
      messageKey: "chat.toolPermission.pending.message",
    };
  }
  if (part.decision === "allow_once") {
    return {
      titleKey: "chat.toolPermission.allowedOnce.title",
      messageKey: "chat.toolPermission.allowedOnce.message",
    };
  }
  if (part.decision === "allow_for_run") {
    return {
      titleKey: "chat.toolPermission.allowedForRun.title",
      messageKey: "chat.toolPermission.allowedForRun.message",
    };
  }
  if (part.decision === "deny") {
    return {
      titleKey: "chat.toolPermission.denied.title",
      messageKey: "chat.toolPermission.denied.message",
    };
  }
  return {
    titleKey: "chat.toolPermission.decided.title",
    messageKey: "chat.toolPermission.decided.message",
  };
}

export function syncToolPermissionCardState(
  part: ToolPermissionPart,
  currentError: string | null,
): ToolPermissionCardState {
  const isDecided = part.status === "decided" || Boolean(part.decision);
  return {
    status: part.status,
    decision: part.decision,
    error: isDecided ? null : currentError,
  };
}
