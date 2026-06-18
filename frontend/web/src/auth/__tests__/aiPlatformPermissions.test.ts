import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { Permission } from "../../types/auth.ts";
import {
  hasEffectivePermission,
  normalizePrincipalPermissions,
} from "../aiPlatformPermissions.ts";

test("keeps ai-platform principal permissions and derives existing UI permissions", () => {
  const permissions = normalizePrincipalPermissions([
    "agent:use",
    "artifact:download",
    "admin:status",
  ]);

  assert.ok(permissions.includes(Permission.AGENT_USE));
  assert.ok(permissions.includes(Permission.ARTIFACT_DOWNLOAD));
  assert.ok(permissions.includes(Permission.ADMIN_STATUS));
  assert.ok(permissions.includes(Permission.CHAT_READ));
  assert.ok(permissions.includes(Permission.CHAT_WRITE));
  assert.ok(permissions.includes(Permission.SESSION_READ));
  assert.ok(permissions.includes(Permission.SESSION_WRITE));
  assert.ok(permissions.includes(Permission.AGENT_READ));
  assert.ok(permissions.includes(Permission.FILE_UPLOAD_DOCUMENT));
  assert.ok(!permissions.includes(Permission.USER_READ));
  assert.ok(!permissions.includes(Permission.ROLE_MANAGE));
  assert.ok(!permissions.includes(Permission.MCP_READ));
});

test("admin status unlocks runtime viewing without legacy settings management", () => {
  const permissions = normalizePrincipalPermissions(["admin:status"]);

  assert.ok(hasEffectivePermission(permissions, Permission.ADMIN_STATUS));
  assert.ok(!hasEffectivePermission(permissions, Permission.SETTINGS_MANAGE));
});

test("unknown permissions are ignored but known legacy permissions are retained", () => {
  const permissions = normalizePrincipalPermissions([
    "skill:read",
    "unknown:permission",
  ]);

  assert.deepEqual(permissions, [Permission.SKILL_READ]);
});

test("auth state reset clears effective permissions with token and user", () => {
  const useAuthSource = readFileSync(
    new URL("../../hooks/useAuth.tsx", import.meta.url),
    "utf8",
  );
  const resetBlocks =
    useAuthSource.match(/setToken\(null\);\s+setUser\(null\);[\s\S]{0,80}/g) ??
    [];

  assert.ok(resetBlocks.length >= 4);
  for (const block of resetBlocks) {
    assert.match(block, /setDynamicPermissions\(\[\]\)/);
  }
});
