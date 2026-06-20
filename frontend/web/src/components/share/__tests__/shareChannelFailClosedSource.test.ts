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

test("launchpad copy keeps click-through boundary visible", () => {
  const launchpad = readFileSync(
    join(root, "src/components/launchpad/LaunchpadPanel.tsx"),
    "utf8",
  );
  assert.match(launchpad, /launchpad\.boundary/);
  assert.doesNotMatch(launchpad, /migrate.*nonGMPlims/i);
});
