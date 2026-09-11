export const INTERNAL_AI_APPLICATIONS = {
  "sop-assistant": {
    agentId: "sop-assistant",
    name: "知识库问答",
  },
  "word-review": {
    agentId: "qa-word-review",
    name: "文档审核",
  },
} as const;

export function resolveInternalAiApplication(appKey: string | undefined) {
  return appKey ? INTERNAL_AI_APPLICATIONS[appKey as keyof typeof INTERNAL_AI_APPLICATIONS] : undefined;
}
