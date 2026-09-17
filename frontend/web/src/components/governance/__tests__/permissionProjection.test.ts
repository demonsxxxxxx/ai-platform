import assert from "node:assert/strict";
import test from "node:test";
import { Permission } from "../../../types";
import { hasEffectivePermission } from "../permissionProjection";

test("skill admin grants public skill permissions", () => {
  const permissions = [Permission.SKILL_ADMIN];

  assert.equal(hasEffectivePermission(permissions, Permission.SKILL_READ), true);
  assert.equal(hasEffectivePermission(permissions, Permission.SKILL_WRITE), true);
  assert.equal(
    hasEffectivePermission(permissions, Permission.SKILL_DELETE),
    true,
  );
});
