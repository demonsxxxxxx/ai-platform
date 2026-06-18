import test from "node:test";
import assert from "node:assert/strict";

import { Permission } from "../../../../types/auth.ts";
import {
  PHASE_2_TABS,
  canShowSurfaceInNavigation,
  getRoutePermissions,
  getSurfacePolicy,
} from "../phase1SurfacePolicy.ts";

test("classifies only backend-missing surfaces as Phase 2", () => {
  assert.deepEqual(PHASE_2_TABS, [
    "marketplace",
    "users",
    "roles",
    "feedback",
    "channels",
    "files",
    "persona",
  ]);

  for (const tab of PHASE_2_TABS) {
    const policy = getSurfacePolicy(tab);
    assert.equal(policy.classification, "phase-2-backend");
    assert.equal(policy.render, "phase2-unavailable");
  }
});

test("remaps already-backed admin projections into Phase 1 panels", () => {
  const remapped = [
    ["skills", Permission.AGENT_ADMIN],
    ["mcp", Permission.ADMIN_STATUS],
    ["agents", Permission.AGENT_ADMIN],
    ["models", Permission.MODEL_ADMIN],
    ["notifications", Permission.ADMIN_STATUS],
  ] as const;

  for (const [tab, permission] of remapped) {
    const policy = getSurfacePolicy(tab);
    assert.equal(policy.classification, "remap-current");
    assert.equal(policy.render, tab);
    assert.deepEqual(getRoutePermissions(tab), [permission]);
    assert.equal(canShowSurfaceInNavigation(tab, [permission]), true);
  }
});

test("settings route is remapped to current Admin Runtime projections", () => {
  const policy = getSurfacePolicy("settings");

  assert.equal(policy.classification, "remap-current");
  assert.equal(policy.render, "admin-runtime");
  assert.deepEqual(getRoutePermissions("settings"), [
    Permission.ADMIN_STATUS,
    Permission.SETTINGS_MANAGE,
  ]);
});

test("Phase 2 standalone routes are gated only by ai-platform admin status", () => {
  for (const tab of PHASE_2_TABS) {
    assert.deepEqual(getRoutePermissions(tab), [Permission.ADMIN_STATUS]);
  }
});

test("agent use is enough for chat but not unsupported independent pages", () => {
  const permissions = [
    Permission.AGENT_USE,
    Permission.CHAT_READ,
    Permission.CHAT_WRITE,
    Permission.SESSION_READ,
    Permission.SESSION_WRITE,
    Permission.SKILL_READ,
  ];

  assert.equal(canShowSurfaceInNavigation("memory", permissions, true), true);
  assert.equal(canShowSurfaceInNavigation("skills", permissions), false);
  assert.equal(canShowSurfaceInNavigation("files", permissions), false);
  assert.equal(canShowSurfaceInNavigation("persona", permissions), false);
  assert.equal(canShowSurfaceInNavigation("settings", permissions), false);
  assert.equal(canShowSurfaceInNavigation("users", permissions), false);
});
