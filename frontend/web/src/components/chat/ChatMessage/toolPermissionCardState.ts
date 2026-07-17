import type { ToolPermissionPart } from "../../../types";

export interface OrdinaryUserToolPermissionPresentation {
  titleKey: string;
  messageKey: string;
}

/**
 * Render retained permission evidence without exposing a runtime control.
 */
export function getOrdinaryUserToolPermissionPresentation(
  part: ToolPermissionPart,
): OrdinaryUserToolPermissionPresentation {
  if (part.status === "pending") {
    // Upgrade/replay may expose historical pending evidence briefly.  It is
    // never an actionable wait state after the zero-click policy cutover.
    return {
      titleKey: "chat.toolPermission.invalidated.title",
      messageKey: "chat.toolPermission.invalidated.message",
    };
  }
  if (part.status === "expired") {
    return {
      titleKey: "chat.toolPermission.expired.title",
      messageKey: "chat.toolPermission.expired.message",
    };
  }
  if (part.status === "cancelled") {
    return {
      titleKey: "chat.toolPermission.cancelled.title",
      messageKey: "chat.toolPermission.cancelled.message",
    };
  }
  if (part.status === "failed") {
    return {
      titleKey: "chat.toolPermission.terminalFailed.title",
      messageKey: "chat.toolPermission.terminalFailed.message",
    };
  }
  if (part.status === "invalidated") {
    return {
      titleKey: "chat.toolPermission.invalidated.title",
      messageKey: "chat.toolPermission.invalidated.message",
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
