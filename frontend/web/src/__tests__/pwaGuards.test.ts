import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  PWA_SKIP_WAITING_MESSAGE,
  PWA_UPDATE_AVAILABLE_EVENT,
  isPwaSkipWaitingMessage,
  isPwaUpdateReady,
  shouldRegisterPwa,
} from "../pwaGuards.ts";

test("registers the PWA only for production browsers with service worker support", () => {
  assert.equal(
    shouldRegisterPwa({ isProduction: true, hasServiceWorker: true }),
    true,
  );
  assert.equal(
    shouldRegisterPwa({ isProduction: false, hasServiceWorker: true }),
    false,
  );
  assert.equal(
    shouldRegisterPwa({ isProduction: true, hasServiceWorker: false }),
    false,
  );
});

test("reports an installed worker as an update only when a controller exists", () => {
  assert.equal(
    isPwaUpdateReady({ hasController: true, workerState: "installed" }),
    true,
  );
  assert.equal(
    isPwaUpdateReady({ hasController: false, workerState: "installed" }),
    false,
  );
  assert.equal(
    isPwaUpdateReady({ hasController: true, workerState: "installing" }),
    false,
  );
});

test("recognizes the skip waiting message without accepting arbitrary payloads", () => {
  assert.equal(isPwaSkipWaitingMessage(PWA_SKIP_WAITING_MESSAGE), true);
  assert.equal(
    isPwaSkipWaitingMessage({ type: PWA_SKIP_WAITING_MESSAGE }),
    true,
  );
  assert.equal(isPwaSkipWaitingMessage({ type: "OTHER_MESSAGE" }), false);
  assert.equal(isPwaSkipWaitingMessage(null), false);
});

test("uses the ai-platform namespace for PWA update events", () => {
  assert.equal(
    PWA_UPDATE_AVAILABLE_EVENT,
    "ai-platform:pwa-update-available",
  );
});

function readManifest() {
  return JSON.parse(
    readFileSync(resolve(import.meta.dirname, "../../public/manifest.json"), {
      encoding: "utf8",
    }),
  );
}

test("manifest launch colors match the light app shell background", () => {
  const manifest = readManifest() as {
    background_color?: string;
    theme_color?: string;
  };

  assert.equal(manifest.background_color, "#f5f5f4");
  assert.equal(manifest.theme_color, "#f5f5f4");
});

test("manifest exposes install metadata for desktop, tablet, and phone PWAs", () => {
  const manifest = JSON.parse(
    readFileSync(resolve(import.meta.dirname, "../../public/manifest.json"), {
      encoding: "utf8",
    }),
  ) as {
    id?: string;
    scope?: string;
    display?: string;
    display_override?: string[];
    screenshots?: Array<{ form_factor?: string; sizes?: string }>;
    icons?: Array<{ sizes?: string; purpose?: string }>;
  };

  assert.equal(manifest.id, "/");
  assert.equal(manifest.scope, "/");
  assert.equal(manifest.display, "standalone");
  assert.deepEqual(manifest.display_override, [
    "window-controls-overlay",
    "standalone",
    "minimal-ui",
    "browser",
  ]);
  assert.ok(
    manifest.icons?.some(
      (icon) => icon.sizes === "512x512" && icon.purpose === "maskable",
    ),
  );
  assert.ok(
    manifest.screenshots?.some(
      (screenshot) => screenshot.form_factor === "wide",
    ),
  );
  assert.ok(
    manifest.screenshots?.some(
      (screenshot) => screenshot.form_factor === "narrow",
    ),
  );
});
