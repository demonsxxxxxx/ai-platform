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

test("ordinary Skills catalog excludes chat while retaining actual file modes", () => {
  const item = projectOrdinarySkillCatalogItem({
    name: "文档审阅",
    description: "检查文档。",
    inputModes: ["chat", "docx", "pdf"],
  });

  assert.deepEqual(item.applicableFileTypes, ["docx", "pdf"]);
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
