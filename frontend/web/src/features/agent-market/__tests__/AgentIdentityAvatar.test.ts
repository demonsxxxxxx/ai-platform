import assert from "node:assert/strict";
import test from "node:test";

import {
  AGENT_AVATAR_STYLE_OPTIONS,
  buildAgentAvatarUrl,
} from "../../../components/agent/agentAvatar.ts";

test("DiceBear avatars are deterministic, locally rendered data URIs without exposing the seed", async () => {
  const first = await buildAgentAvatarUrl("internal-agent-identity");
  const second = await buildAgentAvatarUrl("internal-agent-identity");
  const different = await buildAgentAvatarUrl("another-agent");

  assert.equal(first, second);
  assert.notEqual(first, different);
  assert.match(first, /^data:image\/svg\+xml/);
  assert.equal(first.includes("internal-agent-identity"), false);
  assert.equal(first.includes("api.dicebear.com"), false);
});

test("each supported avatar style is deterministic and visibly distinct", async () => {
  const sources = await Promise.all(
    AGENT_AVATAR_STYLE_OPTIONS.map(async ({ ref }) => ({
      ref,
      source: await buildAgentAvatarUrl("same-agent", ref),
    })),
  );

  assert.deepEqual(
    sources.map(({ ref }) => ref),
    [
      "builtin:agent",
      "builtin:assistant",
      "builtin:document",
      "builtin:research",
      "builtin:cartoon",
      "builtin:emoji",
      "builtin:pixel",
      "builtin:portrait",
      "builtin:abstract",
      "builtin:planet",
      "builtin:clay",
      "builtin:icon",
    ],
  );
  assert.equal(new Set(sources.map(({ source }) => source)).size, sources.length);
});
