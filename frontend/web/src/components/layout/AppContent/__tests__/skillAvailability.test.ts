import test from "node:test";
import assert from "node:assert/strict";

import {
  buildEffectiveSkills,
  countEnabledSkills,
  buildSkillOptionsFromAgents,
} from "../skillAvailability.ts";
import type { AgentInfo } from "../../../../types";
import type { SkillResponse } from "../../../../types";

function skill(name: string, enabled = true): SkillResponse {
  return {
    name,
    description: "",
    tags: [],
    enabled,
    source: "manual",
    files: {},
    file_count: 1,
    installed_from: "manual",
    is_published: false,
    marketplace_is_active: true,
  };
}

test("limits persona skills by whitelist and then applies disabled skills", () => {
  const result = buildEffectiveSkills({
    skills: [skill("planner"), skill("writer"), skill("other")],
    skillsLoading: false,
    personaSkillNames: ["planner", "writer"],
    disabledSkillNames: ["writer"],
  });

  assert.deepEqual(
    result.map((item) => [item.name, item.enabled]),
    [
      ["planner", true],
      ["writer", false],
    ],
  );
  assert.equal(countEnabledSkills(result), 1);
});

test("falls back to disabled-skills mode without a persona whitelist", () => {
  const result = buildEffectiveSkills({
    skills: [skill("planner"), skill("writer"), skill("globally-off", false)],
    skillsLoading: false,
    disabledSkillNames: ["writer"],
  });

  assert.deepEqual(
    result.map((item) => [item.name, item.enabled]),
    [
      ["planner", true],
      ["writer", false],
    ],
  );
  assert.equal(countEnabledSkills(result), 1);
});

test("builds chat-bound skill options from current agent projections", () => {
  const agents: AgentInfo[] = [
    {
      id: "general-agent",
      name: "General chat",
      description: "Default conversation",
      version: "platform-managed",
    },
    {
      id: "document-review",
      name: "Document review",
      description: "Review uploaded documents",
      version: "platform-managed",
    },
    {
      id: "document-translation",
      name: "Document translation",
      description: "Translate uploaded documents",
      version: "platform-managed",
    },
  ];

  const rawOptions = buildSkillOptionsFromAgents(agents);
  const result = buildEffectiveSkills({
    skills: rawOptions,
    skillsLoading: false,
    disabledSkillNames: ["document-translation"],
  });

  assert.deepEqual(
    result.map((item) => ({
      name: item.name,
      description: item.description,
      enabled: item.enabled,
      source: item.source,
      installed_from: item.installed_from,
      tags: item.tags,
    })),
    [
      {
        name: "document-review",
        description: "Review uploaded documents",
        enabled: true,
        source: "manual",
        installed_from: "manual",
        tags: ["runtime capability"],
      },
      {
        name: "document-translation",
        description: "Translate uploaded documents",
        enabled: false,
        source: "manual",
        installed_from: "manual",
        tags: ["runtime capability"],
      },
    ],
  );
  assert.equal(
    rawOptions.some((item) => item.name === "general-agent"),
    false,
  );
});

test("marks only the current public agent capability as selected", () => {
  const agents: AgentInfo[] = [
    {
      id: "general-agent",
      name: "General chat",
      description: "Default conversation",
      version: "platform-managed",
    },
    {
      id: "document-review",
      name: "Document review",
      description: "Review uploaded documents",
      version: "platform-managed",
    },
    {
      id: "document-translation",
      name: "Document translation",
      description: "Translate uploaded documents",
      version: "platform-managed",
    },
  ];

  const result = buildSkillOptionsFromAgents(agents, "document-translation");

  assert.deepEqual(
    result.map((item) => [item.name, item.enabled]),
    [
      ["document-review", false],
      ["document-translation", true],
    ],
  );
  assert.equal(countEnabledSkills(result), 1);
});
