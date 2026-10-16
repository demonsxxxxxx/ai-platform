export const MCP_JWT_HEADER_NAME = "JWT-Authorization";

export type McpStaticHeaderValidationError =
  | "mcp_header_conflict"
  | "mcp_header_duplicate";

export function validateMcpStaticHeaderNames(
  headers: readonly { key: string }[],
  integrationKind: "mcp" | "enterprise_gateway" = "mcp",
): McpStaticHeaderValidationError | null {
  const reservedNames = new Set([MCP_JWT_HEADER_NAME.toLocaleLowerCase("en-US")]);
  if (integrationKind === "enterprise_gateway") {
    ["authorization", "cookie", "x-mcp-username", "x-mcp-password"].forEach(name => reservedNames.add(name));
  }
  const seen = new Set<string>();

  for (const header of headers) {
    const name = header.key.trim();
    if (!name) continue;
    const normalized = name.toLocaleLowerCase("en-US");
    if (reservedNames.has(normalized)) return "mcp_header_conflict";
    if (seen.has(normalized)) return "mcp_header_duplicate";
    seen.add(normalized);
  }

  return null;
}
