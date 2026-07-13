import test from "node:test";
import assert from "node:assert/strict";
import {
  canAccessWorkbenchItem,
  canAccessWorkbenchPath,
  type WorkbenchAccessKey,
} from "../workbenchAccessPolicy.ts";

const ordinaryUser = { is_admin: false };
const adminUser = { is_admin: true };

const ordinaryItems: WorkbenchAccessKey[] = [
  "chat",
  "apps",
  "skills",
  "mcp",
  "persona",
  "files",
  "agent-workspace",
  "notifications",
  "memory",
];

const adminOnlyItems: WorkbenchAccessKey[] = [
  "users",
  "roles",
  "settings",
  "channels",
  "agents",
  "models",
  "feedback",
];

test("ordinary users receive only the approved workbench destinations", () => {
  for (const item of ordinaryItems) {
    assert.equal(canAccessWorkbenchItem(ordinaryUser, item), true, item);
  }
  for (const item of adminOnlyItems) {
    assert.equal(canAccessWorkbenchItem(ordinaryUser, item), false, item);
  }
});

test("admin users receive both ordinary and management destinations", () => {
  for (const item of [...ordinaryItems, ...adminOnlyItems]) {
    assert.equal(canAccessWorkbenchItem(adminUser, item), true, item);
  }
});

test("admin identity is fail closed unless the signed projection is explicitly true", () => {
  assert.equal(canAccessWorkbenchItem(null, "users"), false);
  assert.equal(canAccessWorkbenchItem({}, "users"), false);
  assert.equal(canAccessWorkbenchItem({ is_admin: false }, "users"), false);
});

test("path policy covers nested management URLs and leaves public unknown paths alone", () => {
  assert.equal(canAccessWorkbenchPath(ordinaryUser, "/channels/slack/demo"), false);
  assert.equal(canAccessWorkbenchPath(ordinaryUser, "/users"), false);
  assert.equal(canAccessWorkbenchPath(ordinaryUser, "/mcp"), true);
  assert.equal(canAccessWorkbenchPath(adminUser, "/channels/slack/demo"), true);
  assert.equal(canAccessWorkbenchPath(ordinaryUser, "/shared/example"), true);
});
