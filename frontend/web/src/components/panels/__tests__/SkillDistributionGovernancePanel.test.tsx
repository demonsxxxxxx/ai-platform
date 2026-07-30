import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("Skill distribution editor uses server-backed ACL controls and safe errors", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SkillDistributionGovernancePanel.tsx"),
    "utf8",
  );

  assert.match(source, /capabilityDistributionApi\.list\("skill"\)/);
  assert.match(source, /capabilityDistributionApi\.update\(/);
  assert.match(source, /data-skill-distribution-visible/);
  assert.match(source, /data-skill-distribution-status/);
  assert.match(source, /data-skill-distribution-departments/);
  assert.match(source, /<RoleSelector/);
  assert.match(source, /普通用户目录和运行准入仍由服务端过滤/);
  assert.doesNotMatch(source, /error instanceof Error \? error\.message/);
});
