import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

test("shared session page has explicit fail-closed states", () => {
  const sharedPage = readFileSync(
    join(root, "src/components/share/SharedPage.tsx"),
    "utf8",
  );
  const unavailable = readFileSync(
    join(root, "src/components/share/ShareUnavailableState.tsx"),
    "utf8",
  );

  assert.match(sharedPage, /ShareUnavailableState/);
  assert.match(unavailable, /denied/);
  assert.match(unavailable, /expired/);
  assert.match(unavailable, /revoked/);
  assert.match(unavailable, /unavailable/);
});

test("channel import page is governed and fail closed", () => {
  const channelPanel = readFileSync(
    join(root, "src/components/channels/ChannelImportPanel.tsx"),
    "utf8",
  );
  const channelsPage = readFileSync(
    join(root, "src/components/pages/ChannelsPage.tsx"),
    "utf8",
  );
  const tabContent = readFileSync(
    join(root, "src/components/layout/AppContent/TabContent.tsx"),
    "utf8",
  );

  assert.match(channelPanel, /channelImport\.unavailable/);
  assert.match(channelPanel, /redaction/);
  assert.match(channelPanel, /retention/);
  assert.match(channelsPage, /ChannelImportPanel/);
  assert.match(tabContent, /ChannelImportPanel/);
  assert.match(tabContent, /channels:\s*ChannelImportPanel/);
});

test("phase 1C discovery pages explain unavailable governance instead of blank denial", () => {
  const skillsHub = readFileSync(
    join(root, "src/components/panels/SkillsHubPanel.tsx"),
    "utf8",
  );
  const mcpPanel = readFileSync(
    join(root, "src/components/panels/MCPPanel.tsx"),
    "utf8",
  );
  const channelPanel = readFileSync(
    join(root, "src/components/channels/ChannelImportPanel.tsx"),
    "utf8",
  );

  assert.match(skillsHub, /statusCopyKey/);
  assert.match(skillsHub, /"permissionLimited"/);
  assert.match(skillsHub, /statusCopyNamespace/);
  assert.match(skillsHub, /data-phase1c-surface="skills-hub"/);
  assert.match(skillsHub, /GovernanceAvailabilityBadge/);
  assert.doesNotMatch(skillsHub, /skills\.featureDisabled/);
  assert.match(mcpPanel, /mcp\.permissionLimited/);
  assert.match(mcpPanel, /data-phase1c-surface="mcp"/);
  assert.match(channelPanel, /data-phase1c-surface="channel-import"/);
});

test("phase 1C governance copy exists in every supported locale", () => {
  for (const locale of ["en", "zh", "ja", "ko", "ru"]) {
    const source = readFileSync(
      join(root, `src/i18n/locales/${locale}.json`),
      "utf8",
    );

    assert.match(source, /"mcp"[\s\S]*"permissionLimited"/, locale);
    assert.match(source, /"skillsHub"[\s\S]*"permissionLimited"/, locale);
    assert.match(source, /"skillsHub"[\s\S]*"featureDisabled"/, locale);
  }
});

test("launchpad copy keeps click-through boundary visible", () => {
  const launchpad = readFileSync(
    join(root, "src/components/launchpad/LaunchpadPanel.tsx"),
    "utf8",
  );
  const enLocale = readFileSync(join(root, "src/i18n/locales/en.json"), "utf8");
  const zhLocale = readFileSync(join(root, "src/i18n/locales/zh.json"), "utf8");

  assert.match(enLocale, /"launchpad"[\s\S]*"boundary"/);
  assert.match(zhLocale, /"launchpad"[\s\S]*"boundary"/);
  assert.doesNotMatch(launchpad, /migrate.*nonGMPlims/i);
});

test("share channel and launchpad use shared workbench unavailable language", () => {
  const unavailable = readFileSync(
    join(root, "src/components/workbench/WorkbenchUnavailableState.tsx"),
    "utf8",
  );
  const share = readFileSync(
    join(root, "src/components/share/ShareUnavailableState.tsx"),
    "utf8",
  );
  const channel = readFileSync(
    join(root, "src/components/channels/ChannelImportPanel.tsx"),
    "utf8",
  );

  assert.match(unavailable, /data-workbench-unavailable/);
  assert.match(share, /WorkbenchUnavailableState/);
  assert.match(channel, /WorkbenchStateSurface/);
  assert.match(channel, /channelApi\.listCatalog/);
  assert.match(channel, /data-channel-catalog-list/);
  assert.match(channel, /channelImport\.unavailable/);
});
