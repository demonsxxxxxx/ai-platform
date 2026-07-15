import assert from "node:assert/strict";
import test from "node:test";

import {
  projectOrdinaryMcpCatalogItem,
  projectOrdinarySkillCatalogItem,
} from "../ordinaryCatalogPolicy.ts";

test("ordinary Skills catalog retains only display metadata", () => {
  const source = {
    name: "文档审阅",
    description: "检查文档并给出可执行建议。",
    inputModes: ["docx", "pdf"],
    content: "private source content",
    fileCount: 24,
    expectedVersion: "hash-123",
    permissionCode: "skill:admin",
  };
  const item = projectOrdinarySkillCatalogItem(source);

  assert.deepEqual(item, {
    displayName: "文档审阅",
    description: "检查文档并给出可执行建议。",
    applicableFileTypes: ["docx", "pdf"],
  });
});

test("ordinary catalog policy rejects malformed and empty public values", () => {
  assert.deepEqual(
    projectOrdinarySkillCatalogItem({
      name: "  ",
      description: null,
      inputModes: "docx",
    }),
    {
      displayName: "",
      description: "",
      applicableFileTypes: [],
    },
  );
});

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
