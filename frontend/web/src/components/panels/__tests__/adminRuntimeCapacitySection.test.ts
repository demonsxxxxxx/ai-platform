import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { shouldFetchAdminRuntimeOverview } from "../adminRuntimeCapacityGuards.ts";

const sectionSource = readFileSync(
  join(import.meta.dirname, "../AdminRuntimeCapacitySection.tsx"),
  "utf8",
);

const settingsPanelSource = readFileSync(
  join(import.meta.dirname, "../SettingsPanel.tsx"),
  "utf8",
);

test("admin runtime capacity section consumes only the ai-platform admin overview projection", () => {
  assert.match(sectionSource, /adminRuntimeApi\.getOverview/);
  assert.match(sectionSource, /Permission\.SETTINGS_MANAGE/);
  assert.match(
    sectionSource,
    /if\s*\(\s*shouldFetchAdminRuntimeOverview\(canView\)\s*\)\s*\{\s*fetchOverview\(\);/s,
  );
  assert.match(
    sectionSource,
    /if\s*\(\s*!shouldFetchAdminRuntimeOverview\(canView\)\s*\)\s*return null;/,
  );
  assert.match(sectionSource, /Capacity/);
  assert.match(sectionSource, /Backpressure/);
  assert.match(sectionSource, /Governance/);
  assert.match(sectionSource, /Load-test evidence/);

  const forbiddenTerms = [
    "executor" + "PrivatePayload",
    "executor_" + "private_payload",
    "raw_" + "payload",
    "storage" + "Key",
    "storage_" + "key",
    "sandbox" + "Workdir",
    "sandbox_" + "workdir",
    "work" + "Dir",
    "work_" + "dir",
    "API" + "_KEY",
    "api" + "_key",
  ];

  for (const term of forbiddenTerms) {
    assert.ok(!sectionSource.includes(term), `section includes ${term}`);
  }
});

test("admin runtime capacity fetch gate is closed without settings management permission", () => {
  assert.equal(shouldFetchAdminRuntimeOverview(false), false);
  assert.equal(shouldFetchAdminRuntimeOverview(true), true);
});

test("settings panel places admin runtime capacity beside system health", () => {
  assert.match(
    settingsPanelSource,
    /import\s+\{\s*AdminRuntimeCapacitySection\s*\}\s+from\s+"\.\/AdminRuntimeCapacitySection";/,
  );
  assert.match(
    settingsPanelSource,
    /<SystemHealthSection\s*\/>\s*<AdminRuntimeCapacitySection\s*\/>/,
  );
});
