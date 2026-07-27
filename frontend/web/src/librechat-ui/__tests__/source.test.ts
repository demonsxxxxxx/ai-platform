import assert from "node:assert/strict";
import test from "node:test";
import { LIBRECHAT_UI_SOURCE } from "../source";

test("LibreChat reference source pin declares the Agent Builder intake scope", () => {
  assert.equal(
    LIBRECHAT_UI_SOURCE.commit,
    "21dc4a2ef490b86510e4b410fe8f78d52c1d9629",
  );
  assert.equal(LIBRECHAT_UI_SOURCE.license, "MIT");
  assert.equal(LIBRECHAT_UI_SOURCE.integrationMode, "reference-derived");
  assert.equal("vendoredScope" in LIBRECHAT_UI_SOURCE, false);

  for (const sourcePath of [
    "client/src/components/SidePanel/Agents/AgentPanel.tsx",
    "client/src/components/SidePanel/Agents/Tools/SkillsDialog.tsx",
    "client/src/components/Agents/AgentGrid.tsx",
  ]) {
    assert.ok(
      (LIBRECHAT_UI_SOURCE.sourcePaths as readonly string[]).includes(sourcePath),
    );
  }
});
