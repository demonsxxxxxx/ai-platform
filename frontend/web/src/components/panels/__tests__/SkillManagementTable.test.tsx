import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("Skill workbench separates runtime and catalog visibility without a public writer", () => {
  const table = readFileSync(
    join(process.cwd(), "src/components/panels/SkillsPanel/SkillManagementTable.tsx"),
    "utf8",
  );
  const list = readFileSync(
    join(process.cwd(), "src/components/panels/SkillsPanel/SkillsList.tsx"),
    "utf8",
  );
  const panel = readFileSync(
    join(process.cwd(), "src/components/panels/SkillsPanel/index.tsx"),
    "utf8",
  );

  assert.match(table, /data-skill-management-table/);
  assert.match(table, /skills\.managementTable\.runtimeStatus/);
  assert.match(table, /skills\.managementTable\.distributed/);
  assert.match(table, /skills\.managementTable\.distributionDisabled/);
  assert.match(table, /entry\.catalogStatus/);
  assert.match(table, /entry\.version/);
  assert.match(table, /entry\.id === selectedSkillId/);
  assert.doesNotMatch(table, /onPublish|publishToMarketplace|republish|unpublish/);
  assert.match(list, /<SkillManagementTable/);
  assert.match(list, /adminRelease \? "btn-primary" : "btn-secondary"/);
  assert.match(list, /skills\.adminReleaseZipTitle/);
  assert.match(list, /canExport=\{canExport && !governedUnavailable\}/);
  assert.match(panel, /const canExportSkills = canEditSkills;/);
  assert.doesNotMatch(
    panel,
    /canExportSkills\s*=.*canAdminUploadSkills/,
  );
  assert.doesNotMatch(list, /<SkillCard/);
});

test("management rows expose stable icon actions and a read-only state", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SkillsPanel/SkillManagementTable.tsx"),
    "utf8",
  );

  assert.match(source, /skills\.managementTable\.disableSkill/);
  assert.match(source, /skills\.managementTable\.enableSkill/);
  assert.match(source, /skills\.managementTable\.editSkill/);
  assert.match(source, /skills\.managementTable\.exportSkill/);
  assert.match(source, /skills\.managementTable\.deleteSkill/);
  assert.match(source, /!hasActions/);
  assert.match(source, /skills\.managementTable\.readOnly/);
  assert.match(
    source,
    /data-label=\{t\("skills\.managementTable\.actions"\)\}/,
  );
  assert.doesNotMatch(source, /[\u4e00-\u9fff]/);
  assert.match(source, /role="table"/);
  assert.match(source, /role="columnheader"/);
});

test("management table translations stay complete across supported locales", () => {
  const requiredKeys = [
    "actions",
    "deleteSkill",
    "disableSkill",
    "distributed",
    "distributionDisabled",
    "editSkill",
    "enableSkill",
    "exportSkill",
    "fileCount",
    "hidden",
    "listLabel",
    "notInDirectory",
    "notPublished",
    "package",
    "packageOnly",
    "readOnly",
    "runtimeStatus",
    "selectSkill",
    "selectedDetail",
    "selectDetailPrompt",
    "skill",
    "tags",
    "catalogStatus",
    "updatedAt",
  ];

  for (const locale of ["en", "ja", "ko", "ru", "zh"]) {
    const catalog = JSON.parse(
      readFileSync(join(process.cwd(), `src/i18n/locales/${locale}.json`), "utf8"),
    ) as { skills?: { managementTable?: Record<string, string> } };
    const table = catalog.skills?.managementTable;
    assert.ok(table, `${locale} management table translations must exist`);
    requiredKeys.forEach((key) =>
      assert.equal(typeof table[key], "string", `${locale}.${key}`),
    );
  }
});
