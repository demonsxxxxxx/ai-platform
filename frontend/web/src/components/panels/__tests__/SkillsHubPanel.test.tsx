import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { projectOrdinarySkillCatalogItem } from "../ordinaryCatalogPolicy.ts";
import { resolveSkillsHubGovernance } from "../SkillsHubPanel/state.ts";
import { resolveSkillCatalogSelection } from "../SkillsPanel/skillCatalogEntries.ts";

test("keeps Skill visibility administration server-governed and ordinary catalog projections safe", () => {
  const adminState = resolveSkillsHubGovernance({
    requestedTab: "skills",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    effectivePermissions: ["skill:admin"],
    effectivePermissionsKnown: true,
    catalogReadResolved: true,
  });
  assert.equal(adminState.pageState, "ready");
  assert.equal(adminState.hasPermission, true);
  assert.equal(adminState.effectivePermissionsSource, "catalog");

  const ordinaryState = resolveSkillsHubGovernance({
    requestedTab: "skills",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    effectivePermissions: ["skill:read"],
    effectivePermissionsKnown: true,
    catalogReadResolved: true,
  });
  assert.equal(ordinaryState.pageState, "forbidden");
  assert.equal(ordinaryState.hasPermission, false);
  assert.equal(ordinaryState.governedUnavailable, true);

  const serverSkill = {
    name: "  文件审阅  ",
    description: "  仅显示公开说明  ",
    inputModes: ["chat", "pdf", 42, "xlsx", "pdf"],
    private_manifest: { token: "must-not-project" },
  };
  assert.deepEqual(
    projectOrdinarySkillCatalogItem(serverSkill),
    {
      displayName: "文件审阅",
      description: "仅显示公开说明",
      applicableFileTypes: ["pdf", "xlsx", "pdf"],
    },
  );

  const source = readFileSync(new URL("../SkillsHubPanel.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /<AvailableSkillsPanel|<SkillDistributionGovernancePanel/);
  assert.match(source, /<SkillsPanel[\s\S]*allAuthorizedCatalog/);
  assert.match(source, /showDistributionEditor=\{isAdmin\}/);
  assert.match(source, /data-primary-page-scroller/);
});

test("canonical Skill page owns one catalog selection and one selected detail", () => {
  const panel = readFileSync(
    new URL("../SkillsPanel/index.tsx", import.meta.url),
    "utf8",
  );
  const list = readFileSync(
    new URL("../SkillsPanel/SkillsList.tsx", import.meta.url),
    "utf8",
  );
  const editor = readFileSync(
    new URL("../SkillDistributionGovernancePanel.tsx", import.meta.url),
    "utf8",
  );

  assert.equal(
    (panel.match(/const \[selectedSkillId, setSelectedSkillId\]/g) ?? []).length,
    1,
  );
  assert.match(panel, /data-selected-skill-detail/);
  assert.match(panel, /selectedSkillId=\{selectedAdminSkill\?\.skillId \?\? null\}/);
  assert.match(list, /data-skills-master-detail/);
  assert.match(list, /data-selected-skill-detail-shell/);
  assert.doesNotMatch(editor, /role="list"|aria-label="Skill 列表"/);
});

test("Skill master-detail selection moves deterministically when the selected Skill disappears", () => {
  const entries = [{ id: "skill-b" }, { id: "skill-c" }];

  assert.deepEqual(resolveSkillCatalogSelection(entries, "skill-b"), {
    selectedSkillId: "skill-b",
    changed: false,
  });
  assert.deepEqual(resolveSkillCatalogSelection(entries, "skill-a"), {
    selectedSkillId: "skill-b",
    changed: true,
  });
  assert.deepEqual(resolveSkillCatalogSelection([], "skill-a"), {
    selectedSkillId: null,
    changed: true,
  });
});

test("Skill archiving removes local catalog rows and announces the synchronized detail selection", () => {
  const panel = readFileSync(
    new URL("../SkillsPanel/index.tsx", import.meta.url),
    "utf8",
  );
  const actions = readFileSync(
    new URL("../SkillsPanel/useSkillsActions.ts", import.meta.url),
    "utf8",
  );
  const skillsHook = readFileSync(
    new URL("../../../hooks/useSkills.ts", import.meta.url),
    "utf8",
  );

  assert.match(panel, /onSkillsArchived: setArchivedSkills/);
  assert.match(panel, /data-skill-selection-status/);
  assert.match(panel, /selectionNotice \?/);
  assert.match(panel, /className="sr-only"/);
  assert.match(panel, /else if \(!selectedSkillId && nextEntry\)/);
  assert.match(panel, /resolveSkillCatalogSelection/);
  assert.match(actions, /setAdminCatalogItems\(\(current\) =>/);
  assert.match(actions, /await refreshAdminSkillCatalog\(\)/);
  assert.match(actions, /options\?\.onSkillsArchived\?\./);
  assert.match(actions, /removeArchivedActionSelections/);
  assert.match(actions, /resolveArchivedSkillCatalogEntries/);
  assert.match(actions, /skills\.batchDeletePartial/);
  assert.match(actions, /isDeleting/);
  assert.match(skillsHook, /Promise<string\[\]>/);
  assert.match(skillsHook, /return result\.deleted/);
  assert.match(skillsHook, /current\.filter\(\(skill\) => skill\.name !== name\)/);
  assert.match(skillsHook, /catalogMutationRevisionRef/);
  assert.match(skillsHook, /pendingCatalogMutationsRef/);
  assert.match(skillsHook, /if \(finishCatalogMutation\(\)\) \{\s*await fetchSkills\(\);/);
});

test("Skill selection synchronization copy exists in Chinese", () => {
  const catalog = JSON.parse(
    readFileSync(new URL("../../../i18n/locales/zh.json", import.meta.url), "utf8"),
  ) as {
    skills?: { managementTable?: Record<string, unknown> };
  };
  const managementTable = catalog.skills?.managementTable;
  assert.ok(managementTable, "Chinese Skill management translations must exist");
  for (const key of [
    "selectionAfterDelete",
    "selectionAfterDeleteEmpty",
    "selectionChanged",
    "selectionCurrent",
  ]) {
    assert.equal(typeof managementTable[key], "string", `zh.skills.managementTable.${key}`);
  }
});

test("Skill management enabled metric uses Chinese enabled-state wording", () => {
  const catalog = JSON.parse(
    readFileSync(new URL("../../../i18n/locales/zh.json", import.meta.url), "utf8"),
  ) as {
    skills?: {
      managementTable?: { enabled?: string };
      metrics?: { enabled?: string };
    };
  };
  const metricsEnabled = catalog.skills?.metrics?.enabled;
  const managementEnabled = catalog.skills?.managementTable?.enabled;
  assert.equal(typeof metricsEnabled, "string", "zh.skills.metrics.enabled");
  assert.equal(
    typeof managementEnabled,
    "string",
    "zh.skills.managementTable.enabled",
  );
  assert.equal(
    metricsEnabled,
    managementEnabled,
    "zh.skills.metrics.enabled",
  );
});

test("Skill catalog refreshes fail pending and hidden batch selections are cleared", () => {
  const hook = readFileSync(
    new URL("../../../hooks/useSkills.ts", import.meta.url),
    "utf8",
  );
  const actions = readFileSync(
    new URL("../SkillsPanel/useSkillsActions.ts", import.meta.url),
    "utf8",
  );

  assert.match(
    hook,
    /setCatalogReadResolved\(false\);\s*setPermissionsValid\(false\);\s*setEffectivePermissionsKnown\(false\);/,
  );
  assert.match(
    actions,
    /setSelectedNames\(new Set\(\)\);\s*\}, \[page, searchQuery, selectedTags\]\);/,
  );
});

test("Skill management keeps admin mutations aligned and always reports the current page", () => {
  const panel = readFileSync(
    new URL("../SkillsPanel/index.tsx", import.meta.url),
    "utf8",
  );
  const list = readFileSync(
    new URL("../SkillsPanel/SkillsList.tsx", import.meta.url),
    "utf8",
  );
  const hook = readFileSync(
    new URL("../../../hooks/useSkills.ts", import.meta.url),
    "utf8",
  );

  assert.match(panel, /const isAiAdmin = isAiAdminUser\(user\)/);
  assert.match(list, /total > 0/);
  assert.match(list, /skills\.paginationSummary/);
  assert.match(hook, /skills\.toggleFailed/);
  assert.match(hook, /skills\.deleteFailed/);
});
