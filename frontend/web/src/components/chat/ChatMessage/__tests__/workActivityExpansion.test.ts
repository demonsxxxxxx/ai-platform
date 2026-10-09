import assert from "node:assert/strict";
import test from "node:test";
import type { MessagePart } from "../../../../types";
import { shouldExpandWorkActivity } from "../workActivityExpansion";

const work: MessagePart = {
  type: "summary", kind: "work_trace", content: "Checking public sources.",
};
const answer: MessagePart = { type: "text", content: "Final answer." };
const classify = (part: MessagePart) =>
  (part.type === "summary" && part.kind === "work_trace") ||
  (part.type === "text" && part.text_role === "work");

test("work details expand during work", () => {
  assert.equal(shouldExpandWorkActivity([work], true, classify), true);
});

test("the first answer text collapses preceding work before Run completion", () => {
  assert.equal(shouldExpandWorkActivity([work, answer], true, classify), false);
});

test("a later work source reopens the automatic work phase", () => {
  assert.equal(shouldExpandWorkActivity([work, answer, work], true, classify), true);
});

test("answer deltas remain in the same automatic phase", () => {
  assert.equal(shouldExpandWorkActivity([work, { ...answer, content: "F" }], true, classify), false);
  assert.equal(shouldExpandWorkActivity([work, answer], true, classify), false);
});

test("the first safe pending preview collapses preceding work immediately", () => {
  assert.equal(
    shouldExpandWorkActivity(
      [work, { type: "text", content: "Preview", logical_id: "part-1", public_part_id: "part-1", text_role: "pending" }],
      true,
      classify,
    ),
    false,
  );
});

test("answer and work classifications select their own phase", () => {
  const pending: MessagePart = {
    type: "text",
    content: "Classified text",
    logical_id: "part-1",
    public_part_id: "part-1",
    text_role: "pending",
  };
  assert.equal(
    shouldExpandWorkActivity([work, { ...pending, text_role: "answer" }], true, classify),
    false,
  );
  assert.equal(
    shouldExpandWorkActivity([answer, { ...pending, text_role: "work" }], true, classify),
    true,
  );
});

test("empty and whitespace-only text does not start the answer phase", () => {
  for (const content of ["", " ", "\n\n"]) {
    assert.equal(shouldExpandWorkActivity([work, { type: "text", content }], true, classify), true);
  }
});

test("nested text does not collapse the parent work phase", () => {
  assert.equal(shouldExpandWorkActivity([work, { ...answer, depth: 1 }], true, classify), true);
});

test("a legacy inline summary does not masquerade as work", () => {
  const summary: MessagePart = { type: "summary", content: "Public summary." };
  assert.equal(shouldExpandWorkActivity([work, answer, summary], true, classify), false);
});

test("terminal and historical messages default to collapsed", () => {
  assert.equal(shouldExpandWorkActivity([work], false, classify), false);
  assert.equal(shouldExpandWorkActivity([work], undefined, classify), false);
});

test("empty streaming message starts in work phase", () => {
  assert.equal(shouldExpandWorkActivity([], true, classify), true);
});
