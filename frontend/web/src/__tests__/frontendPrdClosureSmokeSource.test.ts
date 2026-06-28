import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();
const smokeScript = join(root, "scripts/prd-closure-browser-smoke.mjs");

test("PRD closure browser smoke helper is committed without credentials", () => {
  assert.equal(existsSync(smokeScript), true);
  const source = readFileSync(smokeScript, "utf8");

  assert.match(source, /AI_PLATFORM_LOGIN_USERNAME/);
  assert.match(source, /AI_PLATFORM_LOGIN_PASSWORD/);
  assert.match(source, /readEnvFile/);
  assert.match(source, /redacted/);
  assert.doesNotMatch(
    source,
    /AI_PLATFORM_(?:LOGIN|SMOKE|TEST|FRONTEND_LOGIN)_(?:USERNAME|PASSWORD)\s*=\s*["'][^"']+["']/,
  );
  assert.doesNotMatch(source, /credentials\.(?:username|password)\.value[^;]*writeFileSync/);
});

test("PRD closure browser smoke helper covers required frontend evidence", () => {
  const source = readFileSync(smokeScript, "utf8");

  for (const selector of [
    "data-librechat-shell",
    "data-composer-command-menu",
    "data-composer-command-item",
    "data-composer-skill-selector",
    "data-composer-skill-row",
    "data-composer-mcp-selector",
    "data-composer-mcp-row",
    "data-composer-chip-kind",
    "data-composer-file-reference",
    "data-frontend-governance-state",
  ]) {
    assert.match(source, new RegExp(selector));
  }

  for (const route of [
    "/chat",
    "/apps",
    "/skills",
    "/marketplace",
    "/roles",
    "/mcp",
    "/persona",
    "/files",
    "/channels",
    "/settings",
    "/shared/smoke-denied",
  ]) {
    assert.match(source, new RegExp(route.replace(/\//g, "\\/")));
  }

  assert.match(source, /ordinaryWorkflow/);
  assert.match(source, /adminWorkflow/);
  assert.match(source, /composerEvidence/);
  assert.match(source, /governanceEvidence/);
});

test("PRD closure browser smoke helper accepts MCP denied or unavailable state", () => {
  const source = readFileSync(smokeScript, "utf8");

  assert.match(source, /mcpSelectionEvidence/);
  assert.match(source, /selectedOrDeniedState/);
  assert.match(source, /unavailableRows/);
  assert.match(source, /deniedRows/);
});

test("PRD closure browser smoke helper fails closed on route hydration and file evidence", () => {
  const source = readFileSync(smokeScript, "utf8");

  assert.match(source, /data-authenticated-workbench-page/);
  assert.match(source, /waitForRouteHydration/);
  assert.match(source, /routeEvidenceReady/);
  assert.match(source, /fileEvidenceReady/);
  assert.match(source, /statusReasons/);
});
