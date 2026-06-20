import type { FeaturePanel } from "../selectors/FeatureMenu";

export const COMMAND_PREFIX_PANEL: Record<string, Exclude<FeaturePanel, null>> =
  {
    "/": "skills",
    "$": "tools",
  };

export interface CommandPanelAvailability {
  skills: boolean;
  tools: boolean;
}

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
