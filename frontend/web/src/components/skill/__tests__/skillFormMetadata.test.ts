import assert from "node:assert/strict";
import test from "node:test";
import YAML from "yaml";
import { syncSkillMarkdownMetadata } from "../SkillForm.utils";

function metadata(markdown: string) { return YAML.parseDocument(markdown.split("\n---\n")[0].replace(/^---\n/, "")).toJSON(); }

test("metadata edits preserve unrelated fields, nested YAML, aliases and comments", () => {
  const input = "---\n# deployment owner\nname: old\ndescription: old description\nallowed-tools: [Read, Bash]\nlicense: MIT\nbase: &base\n  retries: 3\nconfig: *base\nmetadata:\n  owner: tools\n  flags: [one, two]\n---\n\n  # Keep body spacing\ntext --- with delimiter inline\n";
  const output = syncSkillMarkdownMetadata(input, "new", "new description", ["tag"]);
  const parsed = metadata(output);
  assert.deepEqual(parsed, { name: "new", description: "new description", "allowed-tools": ["Read", "Bash"], license: "MIT", base: { retries: 3 }, config: { retries: 3 }, metadata: { owner: "tools", flags: ["one", "two"] }, tags: ["tag"] });
  assert.ok(output.includes("# deployment owner"));
  assert.ok(output.endsWith("\n  # Keep body spacing\ntext --- with delimiter inline\n"));
});

test("BOM and CRLF markdown body remain byte-for-byte intact", () => {
  const body = "\r\n\r\n  # Body\r\n```yaml\r\n---\r\n```\r\n";
  const output = syncSkillMarkdownMetadata("\uFEFF---\r\nname: old\r\nlicense: MIT\r\n---\r\n" + body, "new", "description", []);
  assert.ok(output.startsWith("\uFEFF---\r\n"));
  assert.ok(output.endsWith(body));
  assert.ok(output.includes("license: MIT\r\n"));
});

test("inline delimiters and multi-line metadata use YAML serialization", () => {
  const output = syncSkillMarkdownMetadata('---\nname: valid---skill\ndescription: "before---after"\nlicense: MIT\n---\n# Body', "valid---skill", 'first\nsecond: "quoted"', ["a:b", "---"]);
  assert.equal(metadata(output).description, 'first\nsecond: "quoted"');
  assert.equal(metadata(output).license, "MIT");
  assert.ok(output.endsWith("# Body"));
});

test("invalid or non-mapping frontmatter is rejected instead of discarded", () => {
  for (const content of ["---\nname: [unterminated\n---\nbody", "---\n- sequence\n---\nbody", "---\nname: a\nname: b\n---\nbody", "---\nname: missing end", "---"]) {
    assert.throws(() => syncSkillMarkdownMetadata(content, "new", "description", []), /skill_frontmatter_invalid/);
  }
});

test("new markdown keeps body and creates the requested metadata", () => {
  const body = "  # Original body\n\n";
  const output = syncSkillMarkdownMetadata(body, "example", "description", []);
  assert.deepEqual(metadata(output), { name: "example", description: "description", tags: [] });
  assert.ok(output.endsWith(body));
});
