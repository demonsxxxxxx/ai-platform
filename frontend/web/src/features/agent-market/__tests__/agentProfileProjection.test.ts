import assert from "node:assert/strict";
import test from "node:test";

import {
  projectAgentConversationIdentity,
  projectAgentProfilePublicProjection,
} from "../../../types/agentProfile.ts";

const enterpriseFields = {
  welcome_message: "",
  starter_prompts: [],
  capability_summary: "",
  recommended_tasks: [],
  supported_input_types: ["text"],
  supported_file_types: [],
  expected_outputs: [],
  permissions_and_data_access_notice: "",
  avatar_ref: "builtin:agent",
  category: "general",
  published_at: null,
};

test("public profile treats a legacy empty avatar seed as absent", () => {
  const profile = projectAgentProfilePublicProjection({
    agent_id: "agt_review",
    expected_revision: 2,
    name: "Review",
    description: "",
    avatar_seed: "   ",
    ...enterpriseFields,
  });

  assert.equal(profile.avatar_seed, "agt_review");
});

test("conversation identity treats a legacy empty avatar seed as absent", () => {
  const identity = projectAgentConversationIdentity({
    agent_id: "agt_review",
    revision: 2,
    name: "Review",
    description: "",
    avatar_seed: "",
    ...enterpriseFields,
  });

  assert.equal(identity?.avatar_seed, "agt_review");
});

test("avatar seed projection still rejects malformed non-string values", () => {
  assert.throws(
    () =>
      projectAgentProfilePublicProjection({
        agent_id: "agt_review",
        expected_revision: 2,
        name: "Review",
        description: "",
        avatar_seed: 7,
        ...enterpriseFields,
      }),
    /invalid_agent_profile_projection/,
  );
});
