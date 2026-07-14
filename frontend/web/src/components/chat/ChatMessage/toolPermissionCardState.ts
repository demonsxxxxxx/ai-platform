import type { ToolPermissionDecision, ToolPermissionPart } from "../../../types";

export interface ToolPermissionCardState {
  status: ToolPermissionPart["status"];
  decision: ToolPermissionDecision | undefined;
  error: string | null;
}

export interface OrdinaryUserToolPermissionPresentation {
  title: string;
  message: string;
}

/** Convert governed tool access into a product message with no approval control. */
export function getOrdinaryUserToolPermissionPresentation(
  _part: ToolPermissionPart,
): OrdinaryUserToolPermissionPresentation {
  return {
    title: "Action unavailable",
    message:
      "This action could not be completed because it requires additional authorization.",
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
