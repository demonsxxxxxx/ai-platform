import test from "node:test";
import assert from "node:assert/strict";

import {
  deriveGovernedSkillIds,
  listModelCatalogWithSources,
  normalizeActiveNotificationProjectionResponse,
} from "../phase1Projection.ts";
import type { AgentAppProjection } from "../phase1Projection.ts";

test("derives unique governed skill ids from agent app projections", () => {
  const agentApps: AgentAppProjection[] = [
    {
      app_id: "agent-a",
      name: "Agent A",
      mode: "chat",
      default_skill_id: "baoyu-translate",
      allowed_input_types: [],
      output_types: [],
      status: "active",
    },
    {
      app_id: "agent-b",
      name: "Agent B",
      mode: "chat_file",
      default_skill_id: "qa-word-review",
      allowed_input_types: [],
      output_types: [],
      status: "active",
    },
    {
      app_id: "agent-c",
      name: "Agent C",
      mode: "file",
      default_skill_id: "baoyu-translate",
      allowed_input_types: [],
      output_types: [],
      status: "disabled",
    },
    {
      app_id: "agent-d",
      name: "Agent D",
      mode: "chat",
      default_skill_id: "",
      allowed_input_types: [],
      output_types: [],
      status: "active",
    },
  ];

  assert.deepEqual(deriveGovernedSkillIds(agentApps), [
    "baoyu-translate",
    "qa-word-review",
  ]);
});

test("model catalog keeps available models when provider projection is missing", async () => {
  const catalog = await listModelCatalogWithSources({
    listAvailable: async () => ({
      models: [
        {
          id: "gpt-4.1",
          value: "gpt-4.1",
          provider: "openai",
          label: "GPT-4.1",
        },
      ],
      count: 1,
      enabled_count: 1,
    }),
    listProviders: async () => {
      throw new Error("providers route missing");
    },
  });

  assert.equal(catalog.models.length, 1);
  assert.deepEqual(catalog.providers, []);
  assert.equal(catalog.providers_error, "providers route missing");
});

test("active notification projection accepts ai-platform envelope and legacy array", () => {
  const enveloped = normalizeActiveNotificationProjectionResponse({
    notifications: [
      {
        id: "n1",
        title: "Envelope",
        content: "Wrapped active notification",
        level: "info",
      },
    ],
  });
  const array = normalizeActiveNotificationProjectionResponse([
    {
      id: "n2",
      title: "Array",
      content: "Legacy active notification",
      level: "warning",
    },
  ]);

  assert.equal(enveloped.length, 1);
  assert.equal(enveloped[0].title, "Envelope");
  assert.equal(array.length, 1);
  assert.equal(array[0].title, "Array");
});
