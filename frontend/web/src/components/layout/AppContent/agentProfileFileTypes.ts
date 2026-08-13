import type { AgentProfilePublicProjection } from "../../../types/agentProfile";

export function resolveAgentAcceptedFileTypes(
  _profile:
    | Pick<AgentProfilePublicProjection, "supported_input_types" | "supported_file_types">
    | undefined,
): string[] | undefined {
  // Agent attachments are optional task context. Profile file metadata is a
  // public usage hint, never an upload or execution allowlist.
  return undefined;
}
