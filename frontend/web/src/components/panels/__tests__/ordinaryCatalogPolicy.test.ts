import assert from "node:assert/strict";
import test from "node:test";

import {
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
