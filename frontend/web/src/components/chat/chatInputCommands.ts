import type { FeaturePanel } from "../selectors/FeatureMenu";

export type ComposerCommandName =
  | "skill"
  | "mcp"
  | "agent"
  | "model"
  | "file"
  | "context";

export type ComposerCommandPanel = Exclude<FeaturePanel, null> | "model" | "file" | "context";

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
  skill: "skills",
  mcp: "tools",
  agent: "agent",
  model: "model",
  file: "file",
  context: "context",
};

const commandAvailabilityKey: Record<
  ComposerCommandName,
  keyof ComposerCommandAvailability
> = {
  skill: "skills",
  mcp: "tools",
  agent: "agents",
  model: "models",
  file: "files",
  context: "context",
};

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
    files: availability.files ?? true,
    context: availability.context ?? false,
  };
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
  const [rawCommand = "", ...queryParts] = body.trimStart().split(/\s+/);
  const command = (
    rawCommand && rawCommand in commandPanelByName ? rawCommand : "skill"
  ) as ComposerCommandName;
  const query =
    command === "skill" && !(rawCommand in commandPanelByName)
      ? body.trimStart()
      : queryParts.join(" ");

  const availabilityKey = commandAvailabilityKey[command];
  return {
    trigger,
    command,
    panel: commandPanelByName[command],
    query,
    unavailable: !normalizedAvailability[availabilityKey],
  };
}
