import type { FeaturePanel } from "../selectors/FeatureMenu";

export type ComposerCommandName =
  | "menu"
  | "skill"
  | "mcp"
  | "agent"
  | "model"
  | "file"
  | "context";

export type ComposerCommandPanel =
  | Exclude<FeaturePanel, null>
  | "command-menu"
  | "model"
  | "file"
  | "context";

export interface ComposerCommandAvailability {
  skills: boolean;
  tools: boolean;
  agents: boolean;
  models: boolean;
  files: boolean;
  context: boolean;
}

export interface ParsedComposerCommand {
  trigger: "/" | "$";
  command: ComposerCommandName;
  panel: ComposerCommandPanel;
  query: string;
  unavailable: boolean;
}

export interface ComposerCommandDraft {
  command: ParsedComposerCommand;
  panel: FeaturePanel | "command-menu";
  selectorQuery: string;
  shouldExecute: boolean;
}

export const COMMAND_PREFIX_PANEL: Record<string, Exclude<FeaturePanel, null>> =
  {
    "/": "skills",
    "$": "skills",
  };

export interface CommandPanelAvailability {
  skills: boolean;
  tools: boolean;
  agents?: boolean;
  models?: boolean;
  files?: boolean;
  context?: boolean;
}

const commandPanelByName: Record<ComposerCommandName, ComposerCommandPanel> = {
  menu: "command-menu",
  skill: "skills",
  mcp: "tools",
  agent: "agent",
  model: "model",
  file: "file",
  context: "context",
};

const commandAvailabilityKey: Record<
  Exclude<ComposerCommandName, "menu">,
  keyof ComposerCommandAvailability
> = {
  skill: "skills",
  mcp: "tools",
  agent: "agents",
  model: "models",
  file: "files",
  context: "context",
};

const panelCommandNames = new Set<ComposerCommandName>([
  "skill",
  "mcp",
  "agent",
  "model",
  "context",
]);

export interface SlashCommandMenuItem {
  command: Exclude<ComposerCommandName, "menu">;
  panel: Exclude<ComposerCommandPanel, "command-menu">;
  query: string;
  unavailable: boolean;
  labelKey: string;
  descriptionKey: string;
}

const slashCommandItems: Array<
  Pick<SlashCommandMenuItem, "command" | "panel" | "labelKey" | "descriptionKey">
> = [
  {
    command: "skill",
    panel: "skills",
    labelKey: "composerCommand.skill.label",
    descriptionKey: "composerCommand.skill.description",
  },
  {
    command: "mcp",
    panel: "tools",
    labelKey: "composerCommand.mcp.label",
    descriptionKey: "composerCommand.mcp.description",
  },
  {
    command: "agent",
    panel: "agent",
    labelKey: "composerCommand.agent.label",
    descriptionKey: "composerCommand.agent.description",
  },
  {
    command: "model",
    panel: "model",
    labelKey: "composerCommand.model.label",
    descriptionKey: "composerCommand.model.description",
  },
  {
    command: "file",
    panel: "file",
    labelKey: "composerCommand.file.label",
    descriptionKey: "composerCommand.file.description",
  },
  {
    command: "context",
    panel: "context",
    labelKey: "composerCommand.context.label",
    descriptionKey: "composerCommand.context.description",
  },
];

/** Resolve a typed command prefix only when its target selector is available. */
export function resolveCommandPrefixPanel(
  input: string,
  availability: CommandPanelAvailability,
): FeaturePanel {
  const commandPanel = COMMAND_PREFIX_PANEL[input];
  if (!commandPanel) return null;
  if (commandPanel === "skills" && !availability.skills) return null;
  if (commandPanel === "tools" && !availability.tools) return null;
  return commandPanel;
}

function normalizeAvailability(
  availability: CommandPanelAvailability,
): ComposerCommandAvailability {
  return {
    skills: availability.skills,
    tools: availability.tools,
    agents: availability.agents ?? true,
    models: availability.models ?? false,
    files: availability.files ?? false,
    context: availability.context ?? false,
  };
}

function isComposerCommandName(
  value: string,
): value is Exclude<ComposerCommandName, "menu"> {
  return value in commandAvailabilityKey;
}

function shouldShowSlashMenu(input: string): boolean {
  const body = input.trimStart().slice(1);
  if (!body.trim()) return true;
  const firstToken = body.trimStart().split(/\s+/)[0] ?? "";
  return !isComposerCommandName(firstToken);
}

/** Build the visible `/` command choices without opening any backend surface. */
export function resolveSlashCommandMenu(
  input: string,
  availability: CommandPanelAvailability,
): SlashCommandMenuItem[] {
  const trimmedStart = input.trimStart();
  if (!trimmedStart.startsWith("/")) return [];
  const normalizedAvailability = normalizeAvailability(availability);
  const body = trimmedStart.slice(1);
  const query = body.trimStart().split(/\s+/)[0]?.toLowerCase() ?? "";

  return slashCommandItems
    .filter((item) => item.command.includes(query))
    .map((item) => ({
      ...item,
      query: "",
      unavailable: !normalizedAvailability[commandAvailabilityKey[item.command]],
    }));
}

export function parseComposerCommand(
  input: string,
  availability: CommandPanelAvailability,
): ParsedComposerCommand | null {
  const trimmedStart = input.trimStart();
  const trigger = trimmedStart[0];
  if (trigger !== "/" && trigger !== "$") return null;

  const normalizedAvailability = normalizeAvailability(availability);

  if (trigger === "$") {
    const query = trimmedStart.slice(1).trimStart();
    return {
      trigger,
      command: "skill",
      panel: "skills",
      query,
      unavailable: !normalizedAvailability.skills,
    };
  }

  const body = trimmedStart.slice(1);
  if (shouldShowSlashMenu(trimmedStart)) {
    return {
      trigger,
      command: "menu",
      panel: "command-menu",
      query: body.trimStart(),
      unavailable: false,
    };
  }

  const [rawCommand = "", ...queryParts] = body.trimStart().split(/\s+/);
  const command = rawCommand as Exclude<ComposerCommandName, "menu">;
  const query = queryParts.join(" ");

  const availabilityKey = commandAvailabilityKey[command];
  const unavailable =
    command === "file" && query.trim()
      ? true
      : !normalizedAvailability[availabilityKey];

  return {
    trigger,
    command,
    panel: commandPanelByName[command],
    query,
    unavailable,
  };
}

function isCompleteCommandWord(
  input: string,
  command: ComposerCommandName,
): boolean {
  const trimmedStart = input.trimStart();
  const body = trimmedStart.slice(1);
  const firstToken = body.trimStart().split(/\s+/)[0] ?? "";
  return firstToken === command;
}

export function resolveComposerCommandDraft(
  input: string,
  availability: CommandPanelAvailability,
): ComposerCommandDraft | null {
  const command = parseComposerCommand(input, availability);
  if (!command) return null;

  const panel =
    command.panel === "command-menu"
      ? "command-menu"
      : command.panel === "skills" ||
          command.panel === "tools" ||
          command.panel === "agent" ||
          command.panel === "model" ||
          command.panel === "context"
        ? command.panel
        : null;
  const shouldExecute =
    command.panel !== "command-menu" &&
    (command.unavailable ||
      (!panelCommandNames.has(command.command) &&
        isCompleteCommandWord(input, command.command)));

  return {
    command,
    panel,
    selectorQuery: command.query,
    shouldExecute,
  };
}
