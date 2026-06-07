import assert from "node:assert/strict";
import test from "node:test";

import { getAppToastSidebarOffset } from "../appToastLayout.ts";

test("uses the rail width as the toast sidebar offset when the sidebar is collapsed", () => {
  assert.equal(
    getAppToastSidebarOffset({ sidebarCollapsed: true }),
    "var(--sidebar-rail-width)",
  );
});

test("uses the full sidebar width as the toast sidebar offset when the sidebar is expanded", () => {
  assert.equal(
    getAppToastSidebarOffset({ sidebarCollapsed: false }),
    "var(--sidebar-width)",
  );
});
