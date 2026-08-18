import type { AgentProfileAvatarRef } from "../../types/agentProfile";

export const AGENT_AVATAR_STYLE_OPTIONS = [
  { ref: "builtin:agent", label: "人物", description: "简洁亲和的专家头像" },
  { ref: "builtin:assistant", label: "机器人", description: "适合自动化与技术专家" },
  { ref: "builtin:document", label: "几何", description: "适合文档与流程专家" },
  { ref: "builtin:research", label: "探索者", description: "适合研究与分析专家" },
] as const satisfies ReadonlyArray<{
  ref: AgentProfileAvatarRef;
  label: string;
  description: string;
}>;

export async function buildAgentAvatarUrl(
  seed: string,
  avatarRef: AgentProfileAvatarRef = "builtin:agent",
): Promise<string> {
  const { renderAgentAvatar } = await import("./agentAvatarRenderer");
  return renderAgentAvatar(seed, avatarRef);
}
