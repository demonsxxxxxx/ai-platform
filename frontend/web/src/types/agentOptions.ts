export type AgentThinkingEffort = "auto" | "low" | "medium" | "high";

export interface AgentOption {
  type: "boolean" | "string" | "number";
  default: boolean | string | number;
  label: string;
  label_key?: string;
  description?: string;
  description_key?: string;
  icon?: string;
  options?: { value: string | number; label?: string; label_key?: string }[];
}

export const CHAT_AGENT_OPTION_DEFINITIONS = {
  enable_thinking: {
    type: "string",
    default: "auto",
    label: "Thinking",
    label_key: "agentOptions.enableThinking.label",
  },
} satisfies Record<string, AgentOption>;
