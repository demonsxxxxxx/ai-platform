import assert from "node:assert/strict";
import test from "node:test";

import { buildAgentAvatarUrl } from "../AgentIdentityAvatar.tsx";

test("DiceBear avatars are deterministic, locally rendered data URIs without exposing the seed", () => {
  const first = buildAgentAvatarUrl("internal-agent-identity");
  const second = buildAgentAvatarUrl("internal-agent-identity");
  const different = buildAgentAvatarUrl("another-agent");

  assert.equal(first, second);
  assert.notEqual(first, different);
  assert.match(first, /^data:image\/svg\+xml/);
  assert.equal(first.includes("internal-agent-identity"), false);
  assert.equal(first.includes("api.dicebear.com"), false);
});
