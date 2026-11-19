import assert from "node:assert/strict";
import test from "node:test";

import { projectOrdinaryMcpCatalogItem } from "../ordinaryCatalogPolicy.ts";

test("ordinary MCP catalog retains server and tool public descriptions only", () => {
  const source = {
    name: "文档工具",
    tools: [
      {
        name: "提取摘要",
        description: "从文档中提取摘要。",
        parameters: [{ name: "file_id", type: "string" }],
        system_disabled: false,
      },
    ],
    transport: "sandbox",
    credentialState: "configured",
    roleQuotas: { user: { daily_limit: 1 } },
  };
  const item = projectOrdinaryMcpCatalogItem(source);

  assert.deepEqual(item, {
    name: "文档工具",
    tools: [{ name: "提取摘要", description: "从文档中提取摘要。" }],
  });
});

test("ordinary MCP catalog retains every public tool returned by governed discovery", () => {
  const tools = Array.from({ length: 51 }, (_, index) => ({
    name: `工具 ${index + 1}`,
    description: `公开说明 ${index + 1}`,
    parameters: [{ name: "internal", type: "string" }],
  }));

  const item = projectOrdinaryMcpCatalogItem({
    name: "完整工具目录",
    tools,
  });

  assert.equal(item.tools.length, 51);
  assert.deepEqual(item.tools.at(-1), {
    name: "工具 51",
    description: "公开说明 51",
  });
});
