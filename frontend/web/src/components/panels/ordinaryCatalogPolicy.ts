export interface OrdinarySkillCatalogItem {
  displayName: string;
  description: string;
  applicableFileTypes: string[];
}

export interface OrdinaryMcpToolItem {
  name: string;
  description: string;
}

export interface OrdinaryMcpCatalogItem {
  name: string;
  tools: OrdinaryMcpToolItem[];
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

/** Selects the public MCP server name and the permitted tool descriptions only. */
export function projectOrdinaryMcpCatalogItem(input: {
  name: unknown;
  tools: unknown;
}): OrdinaryMcpCatalogItem {
  const toolRows = Array.isArray(input.tools) ? input.tools : [];
  const tools = toolRows
    .map((tool) => {
      const candidate = tool as { name?: unknown; description?: unknown };
      return {
        name: normalizedText(candidate.name),
        description: normalizedText(candidate.description),
      };
    })
    .filter((tool) => tool.name.length > 0);

  return {
    name: normalizedText(input.name),
    tools,
  };
}
