import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const panelSource = readFileSync(
  join(import.meta.dirname, "../LaunchpadPanel.tsx"),
  "utf8",
);

test("launchpad panel opens destinations in a new tab", () => {
  assert.match(panelSource, /window\.open\(destination\.href,\s*"_blank"/);
  assert.match(panelSource, /rel="noreferrer"/);
});

test("launchpad panel has tabs, search, and unavailable rendering", () => {
  assert.match(panelSource, /launchpadTabs/);
  assert.match(panelSource, /filterLaunchpadGroups/);
  assert.match(panelSource, /launchpad\.unavailable/);
});

test("launchpad panel localizes page chrome and has mobile group navigation", () => {
  assert.match(panelSource, /useTranslation/);
  assert.match(panelSource, /t\("launchpad\.title"\)/);
  assert.match(panelSource, /t\("launchpad\.searchPlaceholder"\)/);
  assert.match(panelSource, /lg:hidden/);
  assert.match(panelSource, /aria-label=\{t\("launchpad\.groupNavigation"\)\}/);
});

test("launchpad search filters the whole company catalog", () => {
  assert.match(panelSource, /query\.trim\(\) \? launchpadGroups : activeGroups/);
  assert.match(panelSource, /filterLaunchpadGroups\(searchGroups, query\)/);
  assert.match(
    panelSource,
    /navigationGroups = query\.trim\(\) \? visibleGroups : activeGroups/,
  );
  assert.match(panelSource, /navigationGroups\.map\(\(group\) =>/);
});
