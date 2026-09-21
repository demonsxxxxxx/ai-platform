import type { MessagePart } from "../../../types";
import { PUBLIC_TOOL_CATEGORIES } from "../../../generated/publicRunStreamV4";
import { groupPublicExecutionStepsForDisplay } from "../../../hooks/useAgent/publicStreamPresentation";

const PUBLIC_TOOL_DISPLAY_NAMES: Readonly<Record<(typeof PUBLIC_TOOL_CATEGORIES)[number], string>> = {
  skill: "Skill",
  mcp: "MCP",
  read: "Read",
  write: "Write",
  edit: "Edit",
  search: "Search",
  execute: "Execute",
};
export const PUBLIC_SUBAGENT_DISPLAY_NAME = "Sub-agent";
const PUBLIC_SAFE_REF_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$/;
const PUBLIC_DURATION_MAX_MS = 86_400_000;
const PUBLIC_DISPLAY_NAME_MAX_LENGTH = 128;
const PUBLIC_TOOL_STATUSES = new Set(["started", "completed", "failed", "denied"]);
const PUBLIC_SUBAGENT_STATUSES = new Set(["running", "complete", "error", "cancelled"]);

const ACTIONABLE_RUN_STATUS_PATTERN =
  /error|failed|failure|denied|blocked|forbidden|unauthori[sz]ed/i;

const WORK_ACTIVITY_TYPES: ReadonlySet<MessagePart["type"]> = new Set([
  "tool",
  "subagent",
  "execution_step",
  "execution_process",
  "todo",
]);

export function isWorkActivityPart(part: MessagePart): boolean {
  return WORK_ACTIVITY_TYPES.has(part.type);
}

function hasControlCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const code = character.charCodeAt(0);
    return code <= 0x1f || code === 0x7f;
  });
}

export function getPublicToolDisplayName(
  publicCategory: string | undefined,
  publicDisplayName?: string,
): string | null {
  if (!publicCategory) return null;
  if (
    publicCategory === "skill" &&
    publicDisplayName &&
    publicDisplayName === publicDisplayName.trim() &&
    publicDisplayName.length <= PUBLIC_DISPLAY_NAME_MAX_LENGTH &&
    !hasControlCharacter(publicDisplayName)
  ) {
    return publicDisplayName;
  }
  return PUBLIC_TOOL_DISPLAY_NAMES[
    publicCategory as (typeof PUBLIC_TOOL_CATEGORIES)[number]
  ] ?? null;
}

export function isPublicToolPresentation(
  publicOperationId: string | undefined,
  publicCategory: string | undefined,
  status: string | undefined,
  durationMs?: number,
): boolean {
  return Boolean(
    publicOperationId &&
    PUBLIC_SAFE_REF_PATTERN.test(publicOperationId) &&
    publicCategory &&
    getPublicToolDisplayName(publicCategory) !== null &&
    status &&
    PUBLIC_TOOL_STATUSES.has(status) &&
    (durationMs === undefined ||
      (Number.isInteger(durationMs) &&
        durationMs >= 0 &&
        durationMs <= PUBLIC_DURATION_MAX_MS)),
  );
}

export function isPublicSubagentPart(
  part: Extract<MessagePart, { type: "subagent" }>,
): boolean {
  return Boolean(
    part.public_operation_id &&
    PUBLIC_SAFE_REF_PATTERN.test(part.public_operation_id) &&
    part.agent_id === part.public_operation_id &&
    part.agent_name &&
    part.agent_name.length <= 128 &&
    part.status &&
    PUBLIC_SUBAGENT_STATUSES.has(part.status) &&
    (!part.parent_agent_id || PUBLIC_SAFE_REF_PATTERN.test(part.parent_agent_id)) &&
    (!part.current_category ||
      PUBLIC_TOOL_CATEGORIES.some((category) => category === part.current_category)) &&
    (part.duration_ms === undefined ||
      (Number.isInteger(part.duration_ms) &&
        part.duration_ms >= 0 &&
        part.duration_ms <= PUBLIC_DURATION_MAX_MS)) &&
    (part.progress_percent === undefined ||
      (Number.isInteger(part.progress_percent) &&
        part.progress_percent >= 0 &&
        part.progress_percent <= 100)),
  );
}

export function isVisibleMessagePart(part: MessagePart): boolean {
  if (part.type === "thinking" || part.type === "sandbox") {
    return false;
  }
  if (part.type === "tool") {
    return isPublicToolPresentation(
      part.public_operation_id,
      part.public_category,
      part.status,
      part.duration_ms,
    );
  }
  if (part.type === "subagent") {
    return isPublicSubagentPart(part);
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
    if (part.type === "tool") {
      const publicDisplayName = getPublicToolDisplayName(
        part.public_category,
        part.public_display_name,
      );
      if (!publicDisplayName) return [];
      return [{
        type: "tool",
        name: publicDisplayName,
        args: {},
        status: part.status,
        isPending: part.status === "started",
        depth: part.depth,
        public_operation_id: part.public_operation_id,
        ...(part.public_category === "skill"
          ? { public_display_name: publicDisplayName }
          : {}),
        public_category: part.public_category,
        duration_ms: part.duration_ms,
      }];
    }
    if (part.type !== "subagent") {
      return [part];
    }

    const publicOperationId = part.public_operation_id as string;
    return [{
      type: "subagent",
      agent_id: publicOperationId,
      public_operation_id: publicOperationId,
      agent_name: PUBLIC_SUBAGENT_DISPLAY_NAME,
      input: "",
      isPending: part.status === "running",
      depth: part.depth,
      parts: part.parts?.length ? getVisibleMessageParts(part.parts) : [],
      startedAt: part.startedAt,
      completedAt: part.completedAt,
      status: part.status,
      parent_agent_id: part.parent_agent_id,
      duration_ms: part.duration_ms,
      progress_percent: part.progress_percent,
      current_category: part.current_category,
    }];
  });
  return groupPublicExecutionStepsForDisplay(visible);
}
