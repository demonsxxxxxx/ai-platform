import assert from "node:assert/strict";
import test from "node:test";

import type { Project } from "../../../../types";
import { isSidebarProject } from "../projectFilters.ts";

function project(type: Project["type"]): Project {
  return {
    id: `${type}-project`,
    user_id: "user-1",
    name: type,
    type,
    icon: "MessageCircle",
    sort_order: 100,
    created_at: "2026-05-09T00:00:00.000Z",
    updated_at: "2026-05-09T00:00:00.000Z",
  };
}

test("sidebar includes channel projects", () => {
  assert.equal(isSidebarProject(project("channel")), true);
});

test("sidebar keeps favorites in the dedicated favorites slot", () => {
  assert.equal(isSidebarProject(project("favorites")), false);
});
