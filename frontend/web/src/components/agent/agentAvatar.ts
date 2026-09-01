import type { AgentProfileAvatarRef } from "../../types/agentProfile";

export const AGENT_AVATAR_STYLE_OPTIONS = [
  { ref: "builtin:agent", label: "人物", description: "简洁亲和的专家头像" },
  { ref: "builtin:assistant", label: "机器人", description: "适合自动化与技术专家" },
  { ref: "builtin:document", label: "几何", description: "适合文档与流程专家" },
  { ref: "builtin:research", label: "探索者", description: "适合研究与分析专家" },
  { ref: "builtin:cartoon", label: "卡通", description: "轻松鲜明的插画头像" },
  { ref: "builtin:emoji", label: "表情", description: "醒目友好的表情头像" },
  { ref: "builtin:pixel", label: "像素", description: "像素风格的数字头像" },
  { ref: "builtin:portrait", label: "肖像", description: "简洁现代的人物肖像" },
  { ref: "builtin:abstract", label: "抽象", description: "柔和多变的抽象头像" },
  { ref: "builtin:planet", label: "星球", description: "适合探索与创意专家" },
  { ref: "builtin:clay", label: "黏土", description: "立体质感的创意头像" },
  { ref: "builtin:icon", label: "图标", description: "清晰克制的符号头像" },
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
