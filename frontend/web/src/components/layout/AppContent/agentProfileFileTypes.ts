import type { AgentProfilePublicProjection } from "../../../types/agentProfile";

export function resolveAgentAcceptedFileTypes(
  profile:
    | Pick<AgentProfilePublicProjection, "supported_input_types" | "supported_file_types">
    | undefined,
): string[] | undefined {
  if (!profile) return undefined;
  return profile.supported_input_types.includes("file")
    ? profile.supported_file_types
    : [];
}
