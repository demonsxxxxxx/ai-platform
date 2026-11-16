import type { ToolInfo } from "../../types";

type ToolGroupSource = Pick<ToolInfo, "category" | "server" | "upstream_server">;

/** Keep connection identity in a disjoint tagged key; upstream labels are display-only. */
export function groupKeyForTool(tool: ToolGroupSource): string {
  if (tool.category !== "mcp") return JSON.stringify(["category", tool.category]);
  const source = tool.upstream_server === undefined
    ? ["direct"]
    : tool.upstream_server === null
      ? ["unknown"]
      : ["upstream", tool.upstream_server];
  return JSON.stringify(["mcp", tool.server ?? "unknown", ...source]);
}

/** Return only bounded, public display text; null means no Gateway source was provided. */
export function groupLabelForTool(tool: ToolGroupSource): string | null {
  if (tool.category !== "mcp") return null;
  if (tool.upstream_server === null) return null;
  return tool.upstream_server || tool.server || null;
}
