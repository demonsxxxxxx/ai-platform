import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();
const harnessPath = join(root, "scripts/browser-smoke-harness.mjs");
const scenarioPath = join(root, "scripts/company-rbac-browser-smoke.mjs");

function read(path: string): string {
  return readFileSync(path, "utf8");
}

test("company RBAC smoke consumes the scenario-neutral browser harness", () => {
  assert.equal(existsSync(harnessPath), true, "shared harness must exist");
  const harness = read(harnessPath);
  const scenario = read(scenarioPath);

  assert.match(harness, /export class CdpClient/);
  assert.match(harness, /export async function startBrowser/);
  assert.match(harness, /export async function captureScreenshot/);
  assert.match(harness, /mkdtempSync/);
  assert.match(harness, /--remote-debugging-port=/);
  assert.match(harness, /Page\.captureScreenshot/);
  assert.match(harness, /child\?\.kill\(\)/);
  assert.match(harness, /waitForChildExit/);
  assert.match(harness, /browser_profile_cleaned/);
  assert.match(harness, /rmSync\(profile/);
  assert.match(harness, /redact/);

  assert.match(scenario, /from "\.\/browser-smoke-harness\.mjs"/);
  assert.match(scenario, /startBrowser\(/);
  assert.match(scenario, /captureScreenshot\(/);
  assert.match(scenario, /report: recordBrowserEvent/);
  assert.doesNotMatch(scenario, /class CdpClient/);
  assert.doesNotMatch(scenario, /--remote-debugging-port=/);
  assert.doesNotMatch(scenario, /mkdtempSync/);
  assert.doesNotMatch(scenario, /node:child_process/);
  assert.doesNotMatch(scenario, /\/json\/version/);
});

test("company RBAC scenario retains exact role, navigation, route, and layout assertions", () => {
  const scenario = read(scenarioPath);

  assert.match(
    scenario,
    /\["chat:read", "chat:write", "session:read", "session:write", "user:read"\]/,
  );
  assert.match(
    scenario,
    /\["chat:read", "chat:write", "session:read", "session:write"\]/,
  );
  assert.match(scenario, /const adminItems = \["channels", "agents", "models"\]/);
  assert.match(scenario, /Math\.max\(navigation\.railCount, navigation\.panelCount\) === 3/);
  assert.match(scenario, /navigation\.railCount === 0 && navigation\.panelCount === 0/);
  assert.match(scenario, /layout\.finalPath === "\/users" && layout\.adminContentSeen === true/);
  assert.match(scenario, /layout\.finalPath === "\/chat" && layout\.adminContentSeen === false/);
  assert.match(scenario, /layout\.bodyScrollWidth <= layout\.width/);
  assert.match(scenario, /layout\.overlaps\.length === 0/);
});

test("shared harness cleans partial startup and reports profile cleanup failure", () => {
  const harness = read(harnessPath);

  assert.match(harness, /await close\(\);\s*throw error;/);
  assert.match(harness, /browser_profile_cleanup_failed/);
  assert.match(harness, /for \(let attempt = 0; attempt < 10; attempt \+= 1\)/);
  assert.match(harness, /if \(!existsSync\(profile\)\)/);
});
