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
  assert.match(source, /if \(!isAiAdminUser\(user\)\) \{[\s\S]*?<AvailableSkillsPanel/);
  assert.match(source, /<SkillDistributionGovernancePanel\s*\/>/);
});
