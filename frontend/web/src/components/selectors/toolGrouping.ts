import type { ToolState } from "../../types";

/** Keep connection identity in a disjoint tagged key; upstream labels are display-only. */
export function groupKeyForTool(tool: ToolState): string {
  if (tool.category !== "mcp") return JSON.stringify(["category", tool.category]);
  const source = tool.upstream_server === undefined
    ? ["direct"]
    : tool.upstream_server === null
      ? ["unknown"]
      : ["upstream", tool.upstream_server];
  return JSON.stringify(["mcp", tool.server ?? "unknown", ...source]);
}

/** Return only bounded, public display text; null means no Gateway source was provided. */
export function groupLabelForTool(tool: ToolState): string | null {
  if (tool.category !== "mcp") return null;
  if (tool.upstream_server === null) return null;
  return tool.upstream_server || tool.server || null;
}
