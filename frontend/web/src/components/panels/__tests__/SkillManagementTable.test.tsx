import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { installTestDom } from "../../../hooks/useAgent/__tests__/testDom.ts";
import { buildSkillCatalogEntries } from "../SkillsPanel/skillCatalogEntries.ts";

const dom = installTestDom();

test("Skill workbench prioritizes governed ZIP upload and catalog status", () => {
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
  assert.match(list, /setPageSize\(Number\(event\.target\.value\)\)/);
  assert.match(list, /skills\.paginationPageSize/);
  assert.match(list, /skill-catalog-view-switcher/);
  assert.match(list, /skill-management-header/);
  assert.doesNotMatch(list, /data-skill-management-metrics/);
  assert.match(panel, /catalogView/);
  assert.match(panel, /setPage=\{setCatalogPage\}/);
  assert.match(panel, /setPageSize=\{actions\.setPageSize\}/);
  assert.match(list, /<SkillManagementTable/);
  assert.match(list, /adminRelease \? "btn-primary" : "btn-secondary"/);
  assert.match(list, /skills\.zipUploadAction/);
  assert.doesNotMatch(list, /GitHub|importFromGitHub/);
  assert.match(list, /resolveSkillCatalogMetrics\(metricsCatalogEntries\)/);
  assert.match(list, /canExport=\{canExport && !governedUnavailable\}/);
  assert.match(panel, /const canExportSkills = canEditSkills;/);
  assert.match(panel, /metricsCatalogEntries=\{catalogEntries\}/);
  assert.doesNotMatch(
    panel,
    /canExportSkills\s*=.*canAdminUploadSkills/,
  );
  assert.doesNotMatch(list, /<SkillCard/);
});

test("management rows expose a focused update action, governed overflow actions, and a read-only state", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SkillsPanel/SkillManagementTable.tsx"),
    "utf8",
  );

  assert.match(source, /skills\.managementTable\.updateVersionSkill/);
  assert.match(source, /MoreHorizontal/);
  assert.match(source, /role="menu"/);
  assert.match(source, /role="menuitem"/);
  assert.match(source, /setOpenActionName/);
  assert.match(source, /onToggle\(actionName\)/);
  assert.match(source, /onEdit\(entry\.runtimeSkill!\)/);
  assert.match(source, /onExportZip\(actionName\)/);
  assert.match(source, /onDelete\(actionName\)/);
  assert.match(source, /!hasActions/);
  assert.match(source, /data-catalog-status=\{entry\.catalogStatus\}/);
  assert.match(source, /skills\.managementTable\.internalRuntime/);
  assert.match(source, /skills\.managementTable\.internalDependency/);
  assert.match(source, /className="block truncate font-mono/);
  assert.match(source, /title=\{entry\.version \?\? undefined\}/);
  assert.match(source, /CatalogStatusIcon/);
  assert.match(source, /skills\.managementTable\.readOnly/);
  assert.match(
    source,
    /data-label=\{t\("skills\.managementTable\.actions"\)\}/,
  );
  assert.doesNotMatch(source, /[\u4e00-\u9fff]/);
  assert.match(source, /role="table"/);
  assert.match(source, /role="columnheader"/);
  assert.match(source, /isInteractiveRowTarget\(event\.target, event\.currentTarget\)/);
  assert.match(source, /target\.closest\(INTERACTIVE_ROW_TARGET\)/);
});

test("Chinese management table translations stay complete", () => {
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
    "internal",
    "internalDependency",
    "internalRuntime",
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
    "updateVersion",
    "updateVersionSkill",
  ];

  const locale = "zh";
  const catalog = JSON.parse(
    readFileSync(join(process.cwd(), "src/i18n/locales/zh.json"), "utf8"),
  ) as { skills?: { managementTable?: Record<string, string> } };
  const table = catalog.skills?.managementTable;
  assert.ok(table, "Chinese management table translations must exist");
  requiredKeys.forEach((key) =>
    assert.equal(typeof table[key], "string", `${locale}.${key}`),
  );
});

test("archive menu action keyboard activation does not select its containing row", async () => {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { SkillManagementTable } = await import(
    "../SkillsPanel/SkillManagementTable.tsx"
  );
  const [entry] = buildSkillCatalogEntries(
    [
      {
        name: "review-skill",
        expected_version: "version-1",
        input_modes: ["chat"],
        requires_file: false,
        description: "Review documents",
        tags: [],
        enabled: true,
        source: "marketplace",
        files: {},
        file_count: 1,
        installed_from: "marketplace",
        is_published: true,
        marketplace_is_active: true,
      },
    ],
    [],
  );
  const container = dom.document.createElement("div");
  const root = createRoot(container as never);
  let archiveCalls = 0;
  let detailSelectionCalls = 0;

  try {
    await React.act(async () => {
      root.render(
        React.createElement(SkillManagementTable, {
          canBatch: true,
          canDelete: true,
          canEdit: false,
          canExport: false,
          canPublish: false,
          canToggle: false,
          entries: [entry!],
          onDelete: () => {
            archiveCalls += 1;
          },
          onEdit: () => {},
          onExportZip: () => {},
          onSelectDetail: () => {
            detailSelectionCalls += 1;
          },
          onSelectAll: () => {},
          onSelectSkill: () => {},
          onToggle: () => {},
          onUploadVersion: () => {},
          selectedNames: new Set<string>(),
          selectedSkillId: null,
        }),
      );
    });

    for (const key of ["Enter", " "]) {
      const menuButton = container
        .querySelectorAll("button")
        .find((button) => button.getAttribute("aria-haspopup") === "menu");
      assert.ok(menuButton, "row action menu must render");
      await React.act(async () => {
        menuButton.dispatchEvent({ type: "click", bubbles: true });
      });
      const archiveButton = container
        .querySelectorAll("button")
        .find((button) =>
          `${button.className} ${button.getAttribute("class") ?? ""}`.includes(
            "skill-management-table__archive-action",
          ),
        );
      assert.ok(archiveButton, "archive action must render in the open menu");
      const keydown: {
        type: string;
        key: string;
        bubbles: boolean;
        defaultPrevented?: boolean;
      } = { type: "keydown", key, bubbles: true };
      await React.act(async () => {
        archiveButton.dispatchEvent(keydown);
        if (keydown.defaultPrevented !== true) {
          archiveButton.dispatchEvent({ type: "click", bubbles: true });
        }
      });
    }

    assert.equal(archiveCalls, 2);
    assert.equal(detailSelectionCalls, 0);
  } finally {
    await React.act(async () => root.unmount());
  }
});
