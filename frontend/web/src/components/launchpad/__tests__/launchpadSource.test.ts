import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const panelSource = readFileSync(
  join(import.meta.dirname, "../LaunchpadPanel.tsx"),
  "utf8",
);

test("launchpad panel opens company destinations externally without iframe embedding", () => {
  assert.match(panelSource, /data-company-navigation-shell/);
  assert.match(panelSource, /window\.open\(href,\s*"_blank"/);
  assert.match(panelSource, /tab\.url/);
  assert.match(panelSource, /openUrl\(tab\.url\)/);
  assert.match(panelSource, /"noopener,noreferrer"/);
  assert.doesNotMatch(panelSource, /data-legacy-webui-frame/);
  assert.doesNotMatch(panelSource, /<iframe/);
  assert.doesNotMatch(panelSource, /sandbox=/);
  assert.doesNotMatch(panelSource, /allow="clipboard-read; clipboard-write"/);
  assert.doesNotMatch(panelSource, /handlePreview/);
  assert.doesNotMatch(panelSource, /getLegacyWebUiFrameUrl/);
});

test("launchpad panel has tabs, search, and unavailable rendering", () => {
  assert.match(panelSource, /launchpadTabs/);
  assert.match(panelSource, /filterLaunchpadGroups/);
  assert.match(panelSource, /launchpad\.unavailable/);
  assert.match(panelSource, /useState<LaunchpadTabKey>\("common"\)/);
  assert.doesNotMatch(panelSource, /useState<LaunchpadTabKey>\("lingxi"\)/);
});

test("launchpad panel localizes page chrome and has mobile group navigation", () => {
  assert.match(panelSource, /useTranslation/);
  assert.match(panelSource, /t\("launchpad\.title"\)/);
  assert.match(panelSource, /t\("launchpad\.searchPlaceholder"\)/);
  assert.match(panelSource, /lg:hidden/);
  assert.match(panelSource, /aria-label=\{t\("launchpad\.groupNavigation"\)\}/);
});

test("launchpad renders as a compact authenticated workbench page", () => {
  assert.match(panelSource, /data-launchpad-workbench/);
  assert.match(panelSource, /data-launchpad-results/);
  assert.doesNotMatch(panelSource, /launchpad\.boundary/);
  assert.doesNotMatch(panelSource, /AI Platform is the home entry/);
  assert.doesNotMatch(panelSource, /作为首页入口/);
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
