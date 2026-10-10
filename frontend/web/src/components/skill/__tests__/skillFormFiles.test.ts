import assert from "node:assert/strict";
import test from "node:test";
import { initializeSkillFormFiles, buildSkillFormFileChanges } from "../skillFormFiles";
import type { SkillResponse } from "../../../types/skill";

export function makeSkill(name = "example"): SkillResponse {
  return { name, description: "Example", tags: [], enabled: true, source: "manual", files: {}, content: "", filePaths: ["SKILL.md", "script.py", "image.png"], file_count: 3, installed_from: "manual", is_published: false, marketplace_is_active: true };
}

test("lazy empty content is never evidence of loaded bytes", () => {
  const entries = initializeSkillFormFiles(makeSkill());
  assert.equal(entries.length, 3);
  assert.ok(entries.every((entry) => entry.loaded === false));
  assert.deepEqual(buildSkillFormFileChanges(entries, makeSkill().filePaths!, "authoritative markdown"), {
    files: { "SKILL.md": "authoritative markdown" }, deletedFiles: [],
  });
});

test("loaded binary previews and unedited text do not create overlays", () => {
  const skill = makeSkill();
  skill.files = { "SKILL.md": "body", "script.py": "print('preserve')", "image.png": "[Binary: image/png, 1KB]" };
  skill.binaryFiles = { "image.png": { url: "https://example.test/file", mime_type: "image/png", size: 1024 } };
  const entries = initializeSkillFormFiles(skill);
  assert.equal(entries.find((entry) => entry.path === "image.png")?.content, "");
  assert.deepEqual(buildSkillFormFileChanges(entries, skill.filePaths!, "new metadata"), { files: { "SKILL.md": "new metadata" }, deletedFiles: [] });
});

test("only explicit removal or rename writes deletion tombstones", () => {
  const skill = makeSkill();
  const entries = initializeSkillFormFiles(skill).filter((entry) => entry.path !== "image.png");
  const script = entries.find((entry) => entry.path === "script.py")!;
  Object.assign(script, { loaded: true, content: "", dirty: true, path: "renamed.py" });
  assert.deepEqual(buildSkillFormFileChanges(entries, skill.filePaths!, "body"), {
    files: { "SKILL.md": "body", "renamed.py": "" }, deletedFiles: ["script.py", "image.png"],
  });
});

test("creation retains template and new empty files", () => {
  const entries = initializeSkillFormFiles();
  assert.ok(entries[0].content.includes("# Skill Name"));
  assert.equal(entries[0].loaded, true);
  entries.push({ path: "empty.txt", content: "", loaded: true, dirty: true });
  assert.deepEqual(buildSkillFormFileChanges(entries, [], "body"), { files: { "SKILL.md": "body", "empty.txt": "" }, deletedFiles: [] });
});

test("missing existing detail never becomes a default template", () => {
  assert.deepEqual(initializeSkillFormFiles({ ...makeSkill(), filePaths: [], files: {} }), []);
});


test("file patches never both write and delete the main markdown", () => {
  const update = buildSkillFormFileChanges([], ["SKILL.md"], "authoritative main");
  assert.deepEqual(update.deletedFiles, []);
  assert.deepEqual(update.files, { "SKILL.md": "authoritative main" });
});
