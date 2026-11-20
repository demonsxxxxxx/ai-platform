export interface OrdinarySkillCatalogItem {
  displayName: string;
  description: string;
  applicableFileTypes: string[];
}

function normalizedStrings(value: unknown, maximum: number): string[] {
  if (!Array.isArray(value)) return [];

  return value
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, maximum);
}

function normalizedText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** Selects the existing public fields that may appear in the ordinary Skills catalog. */
export function projectOrdinarySkillCatalogItem(input: {
  name: unknown;
  description: unknown;
  inputModes: unknown;
}): OrdinarySkillCatalogItem {
  return {
    displayName: normalizedText(input.name),
    description: normalizedText(input.description),
    applicableFileTypes: normalizedStrings(input.inputModes, 8).filter(
      (inputMode) => inputMode.toLowerCase() !== "chat",
    ),
  };
}
