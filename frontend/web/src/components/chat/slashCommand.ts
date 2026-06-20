import type {
  AgentInfo,
  AgentOption,
  FileCategory,
  SkillResponse,
  ToolState,
} from "../../types";
import type { FeaturePanel } from "../selectors/FeatureMenu";

export type SlashCommandGroup =
  | "skill"
  | "mcp"
  | "agent"
  | "model"
  | "file"
  | "context";

export type ComposerSelectionTokenType = SlashCommandGroup;

export interface ComposerSelectionToken {
  id: string;
  type: ComposerSelectionTokenType;
  label: string;
  description?: string;
  state: "selected" | "unavailable";
}

export interface SlashCommandMatch {
  slashIndex: number;
  query: string;
}

export type SlashCommandOptionKind = "command" | "entity";

export interface SlashCommandOption {
  id: string;
  kind: SlashCommandOptionKind;
  group: SlashCommandGroup;
  label: string;
  command: string;
  description?: string;
  value?: string | number | boolean;
  optionKey?: string;
  disabled?: boolean;
  unavailableReason?: string;
  selected?: boolean;
  nextPanel: FeaturePanel;
}

export interface BuildSlashCommandOptionsArgs {
  query: string;
  skills: SkillResponse[];
  tools: ToolState[];
  agents: Array<Pick<AgentInfo, "id" | "name" | "description">>;
  currentAgent?: string;
  agentOptions?: Record<string, AgentOption>;
  agentOptionValues?: Record<string, boolean | string | number>;
  uploadCategories: FileCategory[];
}

export interface ApplySlashCommandSelectionResult {
  input: string;
  cursorPosition: number;
  token: ComposerSelectionToken | null;
  nextPanel: FeaturePanel;
}

const BASE_COMMANDS: Array<
  Pick<
    SlashCommandOption,
    "id" | "kind" | "group" | "label" | "command" | "description" | "nextPanel"
  > & { disabled?: boolean; unavailableReason?: string }
> = [
  {
    id: "command:skill",
    kind: "command",
    group: "skill",
    label: "Skills",
    command: "/skill",
    description: "Choose an enabled skill for this message.",
    nextPanel: "skills",
  },
  {
    id: "command:mcp",
    kind: "command",
    group: "mcp",
    label: "MCP tools",
    command: "/mcp",
    description: "Choose a governed MCP tool.",
    nextPanel: "tools",
  },
  {
    id: "command:agent",
    kind: "command",
    group: "agent",
    label: "Agents",
    command: "/agent",
    description: "Switch the agent for this run.",
    nextPanel: "agent",
  },
  {
    id: "command:model",
    kind: "command",
    group: "model",
    label: "Models",
    command: "/model",
    description: "Choose an available model or thinking option.",
    nextPanel: "thinking",
  },
  {
    id: "command:file",
    kind: "command",
    group: "file",
    label: "Files",
    command: "/file",
    description: "Attach a file category allowed for your account.",
    nextPanel: null,
  },
  {
    id: "command:context",
    kind: "command",
    group: "context",
    label: "Context",
    command: "/context",
    description: "Context projections are not backed yet.",
    disabled: true,
    unavailableReason: "Context selection is unavailable until a backed projection exists.",
    nextPanel: null,
  },
];

export function findSlashCommandMatch(
  input: string,
  cursorPosition: number,
): SlashCommandMatch | null {
  const beforeCursor = input.slice(0, cursorPosition);
  const slashIndex = beforeCursor.lastIndexOf("/");
  if (slashIndex < 0) return null;
  if (slashIndex > 0 && !/\s/.test(beforeCursor[slashIndex - 1] ?? "")) {
    return null;
  }

  const query = beforeCursor.slice(slashIndex + 1);
  if (/\s/.test(query)) return null;
  return { slashIndex, query };
}

export function buildSlashCommandOptions({
  query,
  skills,
  tools,
  agents,
  currentAgent,
  agentOptions,
  agentOptionValues = {},
  uploadCategories,
}: BuildSlashCommandOptionsArgs): SlashCommandOption[] {
  const normalizedQuery = normalizeQuery(query);
  const baseOptions = BASE_COMMANDS.map((option) => ({ ...option }));
  const entityOptions: SlashCommandOption[] = [
    ...skills.map((skill) => ({
      id: `skill:${skill.name}`,
      kind: "entity" as const,
      group: "skill" as const,
      label: skill.name,
      command: `/skill ${skill.name}`,
      description: skill.description,
      value: skill.name,
      disabled: false,
      unavailableReason: skill.enabled ? undefined : "Select to enable this skill.",
      selected: skill.enabled,
      nextPanel: null,
    })),
    ...tools
      .filter((tool) => tool.category === "mcp")
      .map((tool) => ({
        id: `mcp:${tool.name}`,
        kind: "entity" as const,
        group: "mcp" as const,
        label: tool.name,
        command: `/mcp ${tool.name}`,
        description: tool.description || tool.server,
        value: tool.name,
        disabled: !!tool.system_disabled,
        unavailableReason: tool.system_disabled
          ? "Disabled by admin policy."
          : !tool.enabled
            ? "Select to enable this tool."
            : undefined,
        selected: tool.enabled,
        nextPanel: null,
      })),
    ...agents.map((agent) => ({
      id: `agent:${agent.id}`,
      kind: "entity" as const,
      group: "agent" as const,
      label: agent.name,
      command: `/agent ${agent.name}`,
      description: agent.description,
      value: agent.id,
      selected: agent.id === currentAgent,
      nextPanel: null,
    })),
    ...buildModelOptions(agentOptions, agentOptionValues),
    ...uploadCategories.map((category) => ({
      id: `file:${category}`,
      kind: "entity" as const,
      group: "file" as const,
      label: fileCategoryLabel(category),
      command: `/file ${category}`,
      description: "Open the matching upload action.",
      value: category,
      nextPanel: null,
    })),
  ];

  return [...baseOptions, ...entityOptions].filter((option) =>
    optionMatchesQuery(option, normalizedQuery),
  );
}

export function applySlashCommandSelection(
  input: string,
  match: SlashCommandMatch,
  option: SlashCommandOption,
): ApplySlashCommandSelectionResult {
  if (option.disabled) {
    return {
      input,
      cursorPosition: match.slashIndex + match.query.length + 1,
      token: null,
      nextPanel: null,
    };
  }

  const nextInput =
    input.slice(0, match.slashIndex) +
    input.slice(match.slashIndex + match.query.length + 1);
  const cursorPosition = match.slashIndex;

  if (option.kind === "command") {
    return {
      input: nextInput,
      cursorPosition,
      token: null,
      nextPanel: option.nextPanel,
    };
  }

  const value = String(option.value ?? option.label);
  return {
    input: nextInput,
    cursorPosition,
    token: {
      id: value,
      type: option.group,
      label: option.label,
      description: option.description,
      state: "selected",
    },
    nextPanel: option.nextPanel,
  };
}

export function moveSlashCommandHighlight(
  current: number,
  direction: "up" | "down",
  optionCount: number,
): number {
  if (optionCount <= 0) return 0;
  if (direction === "up") {
    return (current - 1 + optionCount) % optionCount;
  }
  return (current + 1) % optionCount;
}

export function dedupeComposerTokens(
  tokens: ComposerSelectionToken[],
  nextToken: ComposerSelectionToken,
): ComposerSelectionToken[] {
  const key = tokenKey(nextToken);
  return [...tokens.filter((token) => tokenKey(token) !== key), nextToken];
}

function buildModelOptions(
  agentOptions?: Record<string, AgentOption>,
  agentOptionValues: Record<string, boolean | string | number> = {},
): SlashCommandOption[] {
  if (!agentOptions) return [];
  return Object.entries(agentOptions).flatMap(([optionKey, option]) => {
    if (!option.options?.length) return [];
    const selectedValue = agentOptionValues[optionKey] ?? option.default;
    return option.options.map((choice) => ({
      id: `model:${optionKey}:${choice.value}`,
      kind: "entity" as const,
      group: "model" as const,
      label: choice.label ?? String(choice.value),
      command: `/model ${choice.label ?? choice.value}`,
      description: option.description ?? option.label,
      value: choice.value,
      optionKey,
      selected: String(choice.value) === String(selectedValue),
      nextPanel: null,
    }));
  });
}

function optionMatchesQuery(
  option: SlashCommandOption,
  normalizedQuery: string,
): boolean {
  if (!normalizedQuery) return true;
  return [
    option.group,
    option.command.replace(/^\//, ""),
    option.label,
    option.description ?? "",
    String(option.value ?? ""),
  ].some((candidate) => normalizeQuery(candidate).includes(normalizedQuery));
}

function normalizeQuery(value: string): string {
  return value.trim().toLowerCase();
}

function fileCategoryLabel(category: FileCategory): string {
  if (category === "image") return "Images";
  if (category === "video") return "Videos";
  if (category === "audio") return "Audio";
  return "Documents";
}

function tokenKey(token: ComposerSelectionToken): string {
  return `${token.type}:${token.id}`;
}
