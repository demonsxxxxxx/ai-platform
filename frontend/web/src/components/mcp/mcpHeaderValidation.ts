export const MCP_JWT_HEADER_NAME = "JWT-Authorization";

export type McpStaticHeaderValidationError =
  | "mcp_header_conflict"
  | "mcp_header_duplicate";

export function validateMcpStaticHeaderNames(
  headers: readonly { key: string }[],
): McpStaticHeaderValidationError | null {
  const reservedName = MCP_JWT_HEADER_NAME.toLocaleLowerCase("en-US");
  const seen = new Set<string>();

  for (const header of headers) {
    const name = header.key.trim();
    if (!name) continue;
    const normalized = name.toLocaleLowerCase("en-US");
    if (normalized === reservedName) return "mcp_header_conflict";
    if (seen.has(normalized)) return "mcp_header_duplicate";
    seen.add(normalized);
  }

  return null;
}
