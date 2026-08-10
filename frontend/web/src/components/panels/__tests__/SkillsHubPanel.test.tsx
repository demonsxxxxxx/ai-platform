import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { projectOrdinarySkillCatalogItem } from "../ordinaryCatalogPolicy.ts";
import { resolveSkillsHubGovernance } from "../SkillsHubPanel/state.ts";

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

  assert.equal((panel.match(/useState<string \| null>/g) ?? []).length, 1);
  assert.match(panel, /data-selected-skill-detail/);
  assert.match(panel, /selectedSkillId=\{selectedAdminSkill\?\.skillId \?\? null\}/);
  assert.match(list, /data-skills-master-detail/);
  assert.match(list, /data-selected-skill-detail-shell/);
  assert.doesNotMatch(editor, /role="list"|aria-label="Skill 列表"/);
});
