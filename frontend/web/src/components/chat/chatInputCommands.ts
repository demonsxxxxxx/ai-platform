export type ComposerCommandName = "skill" | "mcp" | "model" | "file";
export type ComposerCommandPanel = "skills" | "tools" | "model" | "file";

export interface CommandPanelAvailability {
  skills: boolean;
  tools: boolean;
  models?: boolean;
  files?: boolean;
}

/** Only the slash token immediately before the caret belongs to completion. */
export interface ComposerCommandDraft {
  start: number;
  end: number;
  query: string;
}

export interface SlashCommandMenuItem {
  command: ComposerCommandName;
  panel: ComposerCommandPanel;
  unavailable: boolean;
  labelKey: string;
  descriptionKey: string;
}

const commandAvailabilityKey = {
  skill: "skills",
  mcp: "tools",
  model: "models",
  file: "files",
} as const;

const slashCommandItems: Omit<SlashCommandMenuItem, "unavailable">[] = [
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
];

export function resolveComposerCommandDraft(
  input: string,
  cursor = input.length,
): ComposerCommandDraft | null {
  const before = input.slice(0, cursor);
  const match = /(?:^|\s)\/([a-z]*)$/i.exec(before);
  if (!match) return null;
  return { start: before.length - match[1].length - 1, end: before.length, query: match[1].toLowerCase() };
}

/** Suggestions never classify a message or decide whether it may be sent. */
export function resolveSlashCommandMenu(
  input: string,
  availability: CommandPanelAvailability,
  cursor = input.length,
): SlashCommandMenuItem[] {
  const draft = resolveComposerCommandDraft(input, cursor);
  if (!draft) return [];
  return slashCommandItems
    .filter((item) => item.command.startsWith(draft.query))
    .map((item) => ({
      ...item,
      unavailable: !availability[commandAvailabilityKey[item.command]],
    }));
}

export function removeComposerCommandDraft(
  input: string,
  draft: ComposerCommandDraft,
): string {
  return input.slice(0, draft.start) + input.slice(draft.end);
}
