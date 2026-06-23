import assert from "node:assert/strict";
import test from "node:test";
import { Permission } from "../../../types";
import {
  hasEffectivePermission,
  hasAnyEffectivePermission,
} from "../permissionProjection";

test("skill admin grants public skill permissions", () => {
  const permissions = [Permission.SKILL_ADMIN];

  assert.equal(hasEffectivePermission(permissions, Permission.SKILL_READ), true);
  assert.equal(hasEffectivePermission(permissions, Permission.SKILL_WRITE), true);
  assert.equal(
    hasEffectivePermission(permissions, Permission.SKILL_DELETE),
    true,
  );
});

test("marketplace admin grants marketplace public permissions", () => {
  const permissions = [Permission.MARKETPLACE_ADMIN];

  assert.equal(
    hasEffectivePermission(permissions, Permission.MARKETPLACE_READ),
    true,
  );
  assert.equal(
    hasEffectivePermission(permissions, Permission.MARKETPLACE_PUBLISH),
    true,
  );
});

test("effective permission checks do not cross skill and marketplace domains", () => {
  assert.equal(
    hasEffectivePermission([Permission.SKILL_ADMIN], Permission.MARKETPLACE_READ),
    false,
  );
  assert.equal(
    hasEffectivePermission([Permission.MARKETPLACE_ADMIN], Permission.SKILL_READ),
    false,
  );
});

test("hasAnyEffectivePermission accepts inherited matches", () => {
  assert.equal(
    hasAnyEffectivePermission([Permission.MARKETPLACE_ADMIN], [
      Permission.SKILL_READ,
      Permission.MARKETPLACE_READ,
    ]),
    true,
  );
});
