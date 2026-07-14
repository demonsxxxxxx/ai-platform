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
    "/mcp",
    "/files",
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

test("PRD closure browser smoke waits for route-specific workbench content before screenshots", () => {
  const source = readFileSync(smokeScript, "utf8");

  assert.match(source, /ROUTE_CONTENT_SELECTORS/);
  assert.match(source, /waitForRouteContent/);
  assert.match(source, /ROUTE_CONTENT_SELECTORS\.get\(route\)/);

  for (const [route, selector] of [
    ["/chat", "data-librechat-shell"],
    ["/apps", "data-launchpad-directory-shell"],
    ["/skills", "data-skill-workbench-shell"],
    ["/mcp", "data-mcp-directory-shell"],
    ["/files", "data-files-workbench-shell"],
    ["/settings", "data-workbench-projection-page"],
  ]) {
    assert.match(source, new RegExp(route.replace(/\//g, "\\/")));
    assert.match(source, new RegExp(selector));
  }

  const collectRouteSource = source.slice(
    source.indexOf("async function navigateAndCollectRoute"),
    source.indexOf("async function waitForRouteHydration"),
  );
  const waitForHydrationSource = source.slice(
    source.indexOf("async function waitForRouteHydration"),
    source.indexOf("async function waitForRouteContent"),
  );
  assert.ok(
    collectRouteSource.indexOf("waitForRouteContent") <
      collectRouteSource.indexOf("captureScreenshot"),
  );
  assert.match(waitForHydrationSource, /route_hydration:\$\{route\}/);
  assert.doesNotMatch(waitForHydrationSource, /async function waitForRouteContent/);
});

test("PRD closure browser smoke records every frontend governance state", () => {
  const source = readFileSync(smokeScript, "utf8");

  assert.match(source, /GOVERNANCE_SMOKE_STATES/);
  assert.match(source, /collectGovernanceStateMachineEvidence/);
  assert.match(source, /stateMachineEvidenceReady/);

  for (const state of [
    "logged-out",
    "loading",
    "no-workspace",
    "forbidden",
    "degraded",
    "ready",
  ]) {
    assert.match(source, new RegExp(`["']${state}["']`));
    assert.match(
      source,
      new RegExp(`frontend-governance:\\$\\{state\\}|frontend-governance:${state}`),
    );
  }
});
