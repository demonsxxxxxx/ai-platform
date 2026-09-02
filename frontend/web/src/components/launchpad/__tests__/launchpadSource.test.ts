import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(
  new URL("../LaunchpadPanel.tsx", import.meta.url),
  "utf8",
);

const catalogSource = readFileSync(
  new URL("../catalog.ts", import.meta.url),
  "utf8",
);

const favoritesSource = readFileSync(
  new URL("../favorites.ts", import.meta.url),
  "utf8",
);

test("company navigation follows the welcome-dashboard reference inside the existing shell", () => {
  assert.match(panelSource, /data-company-navigation-shell/);
  assert.match(panelSource, /data-launchpad-dashboard/);
  assert.match(panelSource, /useAuth/);
  assert.match(panelSource, /launchpad\.welcome/);
  assert.match(panelSource, /launchpad\.welcomeSubtitle/);
  assert.match(panelSource, /launchpad\.commonServices/);
  assert.match(panelSource, /launchpad\.aiAssistants/);
  assert.doesNotMatch(panelSource, /<main/);
  assert.doesNotMatch(panelSource, /gradient/);
});

test("company navigation provides searchable responsive website sections", () => {
  assert.match(panelSource, /id="launchpad-search"/);
  assert.match(panelSource, /filterLaunchpadGroups/);
  assert.match(panelSource, /sm:grid-cols-2/);
  assert.match(panelSource, /xl:grid-cols-4/);
  assert.match(panelSource, /scroll-smooth/);
  assert.match(panelSource, /motion-reduce:scroll-auto/);
  assert.match(panelSource, /focus-visible:ring-2/);
});

test("website cards use copied icons and safe external anchors", () => {
  assert.match(panelSource, /getLaunchpadIconUrl/);
  assert.match(panelSource, /<img/);
  assert.match(panelSource, /href=\{entry\.url\}/);
  assert.match(panelSource, /target="_blank"/);
  assert.match(panelSource, /rel="noopener noreferrer"/);
  assert.match(panelSource, /companyNavigation\.openEntry/);
  assert.match(panelSource, /onError=/);
  assert.doesNotMatch(panelSource, /window\.open/);
});

test("favorites use authenticated profile persistence instead of browser storage", () => {
  assert.match(panelSource, /aria-pressed=\{isFavorite\}/);
  assert.match(panelSource, /launchpad\.addFavorite/);
  assert.match(panelSource, /launchpad\.removeFavorite/);
  assert.match(panelSource, /id="launchpad-favorites"/);
  assert.match(panelSource, /authApi[\s\S]{0,40}\.getProfile/);
  assert.match(panelSource, /authApi\.updateMetadata/);
  assert.match(panelSource, /currentUserIdRef\.current !== requestOwnerId/);
  assert.match(panelSource, /favoritesOwnerId === user\?\.id/);
  assert.doesNotMatch(panelSource, /localStorage/);
  assert.match(favoritesSource, /LAUNCHPAD_FAVORITES_METADATA_KEY/);
  assert.match(favoritesSource, /allowedIds\.has/);
});

test("obsolete tab, iframe, and runtime configuration paths stay deleted", () => {
  for (const source of [catalogSource, panelSource]) {
    assert.doesNotMatch(source, /launchpadTabs/);
    assert.doesNotMatch(source, /activeTab/);
    assert.doesNotMatch(source, /Lingxi/);
    assert.doesNotMatch(source, /BrowserRuntimeConfig/);
    assert.doesNotMatch(source, /runtimeUrlKey/);
    assert.doesNotMatch(source, /<iframe/);
    assert.doesNotMatch(source, /VITE_LEGACY/);
  }
});
