import type { ToolPermissionDecision, ToolPermissionPart } from "../../../types";

export interface ToolPermissionCardState {
  status: ToolPermissionPart["status"];
  decision: ToolPermissionDecision | undefined;
  error: string | null;
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
