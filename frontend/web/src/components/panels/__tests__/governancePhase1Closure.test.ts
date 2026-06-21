import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("department skill policy is rendered as a fail-closed group toggle row", () => {
  assert.equal(
    existsSync(
      join(root, "src/components/governance/GroupAvailabilityToggleRow.tsx"),
    ),
    true,
  );

  const toggle = read("src/components/governance/GroupAvailabilityToggleRow.tsx");
  const availability = read("src/components/governance/groupAvailability.ts");

  assert.match(toggle, /data-group-toggle-ui/);
  assert.match(toggle, /department-skill-policy/);
  assert.match(toggle, /backed,/);
  assert.match(toggle, /aria-disabled=\{disabled\}/);
  assert.match(availability, /backed === false/);
  assert.match(availability, /state:\s*"unavailable"/);
});

test("skills and marketplace surfaces expose department availability controls", () => {
  const skillsHub = read("src/components/panels/SkillsHubPanel.tsx");
  const marketplace = read("src/components/panels/MarketplacePanel.tsx");

  assert.match(skillsHub, /GroupAvailabilityToggleRow/);
  assert.match(skillsHub, /skills\.marketplace\.departmentAvailability/);
  assert.match(skillsHub, /data-phase1c-surface="skills-hub"/);
  assert.match(marketplace, /GroupAvailabilityToggleRow/);
  assert.match(marketplace, /skills\.marketplace\.departmentAvailability/);
  assert.match(marketplace, /data-phase1c-surface="marketplace"/);
});

test("skills and marketplace remain catalog shells when backend enablement is unavailable", () => {
  const skillsHub = read("src/components/panels/SkillsHubPanel.tsx");
  const skillsPanel = read("src/components/panels/SkillsPanel/index.tsx");
  const marketplace = read("src/components/panels/MarketplacePanel.tsx");

  assert.doesNotMatch(skillsHub, /if\s*\(!enableSkills\)\s*{\s*return/);
  assert.doesNotMatch(skillsPanel, /if\s*\(!enableSkills\)\s*{\s*return/);
  assert.match(skillsHub, /skillsGloballyDisabled/);
  assert.match(skillsHub, /data-skill-catalog-shell/);
  assert.match(skillsHub, /data-marketplace-catalog-shell/);
  assert.match(skillsPanel, /governedUnavailable/);
  assert.match(marketplace, /governedUnavailable/);
  assert.match(marketplace, /data-marketplace-catalog-shell/);
});

test("mcp lifecycle governance remains visible but not writable", () => {
  const mcp = read("src/components/panels/MCPPanel.tsx");

  assert.match(mcp, /data-phase1c-surface="mcp"/);
  assert.match(mcp, /data-fail-closed-surface="mcp-lifecycle"/);
  assert.match(mcp, /lifecycleAvailability/);
  assert.match(mcp, /mcp\.credentialsUnavailable/);
  assert.match(mcp, /data-mcp-directory-shell/);
  assert.doesNotMatch(mcp, /deleteServer\(|createServer\(|updateCredentials\(/);
});

test("governed marketplace and MCP hooks fail closed before calling APIs", () => {
  const marketplaceHook = read("src/hooks/useMarketplace.ts");
  const mcpHook = read("src/hooks/useMcp.ts");
  const skillsHook = read("src/hooks/useSkills.ts");

  for (const apiName of [
    "installSkill",
    "updateSkill",
    "openPreview",
    "readPreviewFile",
    "createAndPublish",
    "updateMarketplaceSkill",
    "activateSkill",
    "deleteSkill",
    "loadMarketplaceSkillForEdit",
  ]) {
    assert.match(
      marketplaceHook,
      new RegExp(`const ${apiName} = useCallback[\\s\\S]*?if \\(!enabled\\)`),
      `${apiName} must guard enabled=false before marketplace API calls`,
    );
  }

  for (const apiName of [
    "getServer",
    "createServer",
    "updateServer",
    "deleteServer",
    "toggleServer",
    "importServers",
    "exportServers",
    "promoteServer",
    "demoteServer",
  ]) {
    assert.match(
      mcpHook,
      new RegExp(`const ${apiName} = useCallback[\\s\\S]*?if \\(!enabled\\)`),
      `${apiName} must guard enabled=false before MCP API calls`,
    );
  }

  for (const apiName of [
    "getSkill",
    "getFullSkill",
    "createSkill",
    "updateSkill",
    "deleteSkill",
    "toggleSkill",
    "batchDeleteSkills",
    "batchToggleSkills",
    "toggleCategory",
    "toggleAll",
    "uploadSkill",
    "previewZipSkills",
    "previewGitHubSkills",
    "installGitHubSkills",
    "publishToMarketplace",
  ]) {
    assert.match(
      skillsHook,
      new RegExp(`const ${apiName} = useCallback[\\s\\S]*?if \\(!enabled\\)`),
      `${apiName} must guard enabled=false before skills API calls`,
    );
  }

  assert.match(
    skillsHook,
    /const toggleCategory = useCallback\(\s*async \(_category: SkillSource, nextEnabled: boolean\): Promise<boolean> => {\s*if \(!enabled\) return false;/,
    "toggleCategory must guard hook-level enabled before using target state",
  );
  assert.match(
    skillsHook,
    /const toggleAll = useCallback\(\s*async \(nextEnabled: boolean\): Promise<boolean> => {\s*if \(!enabled\) return false;/,
    "toggleAll must guard hook-level enabled before using target state",
  );
});
