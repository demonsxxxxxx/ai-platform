export type ComposerSelectionKind =
  | "skill"
  | "mcp"
  | "model"
  | "file"
  | "context";

export type ComposerSelectionState =
  | "enabled"
  | "disabled"
  | "pending"
  | "denied"
  | "uploading"
  | "unavailable"
  | "admin-only";

export interface ComposerSelection {
  id: string;
  kind: ComposerSelectionKind;
  label: string;
  state: ComposerSelectionState;
  description?: string;
  referenceId?: string;
  visibleDetails?: string[];
  source?: string;
}

export type ComposerSelectionAction =
  | { type: "upsert"; selection: ComposerSelection }
  | { type: "remove"; id: string }
  | { type: "clear-kind"; kind: ComposerSelectionKind }
  | { type: "clear-all" };

export function composerSelectionReducer(
  state: ComposerSelection[],
  action: ComposerSelectionAction,
): ComposerSelection[] {
  switch (action.type) {
    case "upsert": {
      const next = state.filter((item) => item.id !== action.selection.id);
      return [...next, action.selection];
    }
    case "remove":
      return state.filter((item) => item.id !== action.id);
    case "clear-kind":
      return state.filter((item) => item.kind !== action.kind);
    case "clear-all":
      return [];
  }
}

export function createSelectionChip(
  chip: ComposerSelection,
): ComposerSelection {
  return chip;
}

export function removeSelectionChip(
  chips: ComposerSelection[],
  id: string,
): ComposerSelection[] {
  return chips.filter((chip) => chip.id !== id);
}

export type ComposerSelectionType = ComposerSelectionKind;
export type ComposerSelectionStatus = ComposerSelectionState;
export type ComposerSelectionChip = ComposerSelection;
