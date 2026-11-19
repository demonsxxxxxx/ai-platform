export interface OrdinaryMcpToolItem {
  name: string;
  description: string;
}

export interface OrdinaryMcpCatalogItem {
  name: string;
  tools: OrdinaryMcpToolItem[];
}

function normalizedText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
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
