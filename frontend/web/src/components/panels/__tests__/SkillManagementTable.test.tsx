import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("Skill workbench separates runtime and tenant distribution without a public writer", () => {
  const table = readFileSync(
    join(process.cwd(), "src/components/panels/SkillsPanel/SkillManagementTable.tsx"),
    "utf8",
  );
  const list = readFileSync(
    join(process.cwd(), "src/components/panels/SkillsPanel/SkillsList.tsx"),
    "utf8",
  );

  assert.match(table, /data-skill-management-table/);
  assert.match(table, /运行状态/);
  assert.match(table, /租户分发中/);
  assert.match(table, /租户分发已停用/);
  assert.match(table, /skill\.marketplace_is_active/);
  assert.match(table, /skill\.expected_version/);
  assert.doesNotMatch(table, /onPublish|publishToMarketplace|republish|unpublish/);
  assert.match(list, /<SkillManagementTable/);
  assert.match(list, /adminRelease \? "btn-primary" : "btn-secondary"/);
  assert.match(list, /skills\.adminReleaseZipTitle/);
  assert.doesNotMatch(list, /<SkillCard/);
});

test("management rows expose stable icon actions and a read-only state", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SkillsPanel/SkillManagementTable.tsx"),
    "utf8",
  );

  assert.match(source, /aria-label=\{skill\.enabled \? `停用/);
  assert.match(source, /: `启用/);
  assert.match(source, /aria-label=\{`编辑/);
  assert.match(source, /aria-label=\{`导出/);
  assert.match(source, /aria-label=\{`删除/);
  assert.match(source, /!hasActions/);
  assert.match(source, />只读</);
  assert.match(source, /role="table"/);
  assert.match(source, /role="columnheader"/);
});
