import type { AgentProfileAvatarRef } from "../../types/agentProfile";

export const AGENT_AVATAR_STYLE_OPTIONS = [
  { ref: "builtin:agent", label: "人物", description: "简洁亲和的人物头像" },
  { ref: "builtin:assistant", label: "机器人", description: "适合自动化与技术助手" },
  { ref: "builtin:document", label: "几何", description: "适合文档与流程型智能体" },
  { ref: "builtin:research", label: "探索者", description: "适合研究与分析型智能体" },
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
