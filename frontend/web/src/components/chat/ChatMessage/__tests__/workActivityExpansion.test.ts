import assert from "node:assert/strict";
import test from "node:test";
import { shouldExpandWorkActivity } from "../workActivityExpansion";

test("new live operations start open; historical operations start collapsed", () => {
  assert.equal(shouldExpandWorkActivity(true), true);
  assert.equal(shouldExpandWorkActivity(false), false);
  assert.equal(shouldExpandWorkActivity(undefined), false);
});
