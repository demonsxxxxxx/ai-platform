import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { resolveGroupAvailability } from "../../governance/groupAvailability";

const root = process.cwd();

test("group availability has explicit governed states", () => {
  assert.equal(resolveGroupAvailability({ enabled: true }).state, "enabled");
  assert.equal(resolveGroupAvailability({ enabled: false }).state, "disabled");
  assert.equal(resolveGroupAvailability({ inherited: true }).state, "inherited");
  assert.equal(resolveGroupAvailability({ backed: false }).state, "unavailable");
  assert.equal(resolveGroupAvailability({ adminOnly: true }).state, "admin-only");
});

test("skills hub exposes marketplace and group availability language", () => {
  const source = readFileSync(
    join(root, "src/components/panels/SkillsHubPanel.tsx"),
    "utf8",
  );
  assert.match(source, /GovernanceAvailabilityBadge/);
  assert.match(source, /skills\.marketplace\.departmentAvailability/);
  assert.match(source, /skills\.marketplace\.groupToggleUnavailable/);
});

test("mcp panel exposes governed tools without raw lifecycle controls", () => {
  const source = readFileSync(
    join(root, "src/components/panels/MCPPanel.tsx"),
    "utf8",
  );
  assert.match(source, /GovernanceAvailabilityBadge/);
  assert.match(source, /mcp\.permissionMode/);
  assert.match(source, /mcp\.lifecycleUnavailable/);
  assert.doesNotMatch(source, /startServer|stopServer|restartServer|rawCredential/);
});
