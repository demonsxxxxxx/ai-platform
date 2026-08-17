import assert from "node:assert/strict";
import test from "node:test";

import {
  PUBLIC_RUN_STREAM_SCHEMA,
  STREAM_DESIGN_ID,
} from "../../../generated/publicRunStreamV3.ts";
import { adaptPublicRunStreamEventV3 } from "../publicRunStreamV3.ts";

function envelope(
  eventType: string,
  payload: Record<string, unknown>,
  overrides: Record<string, unknown> = {},
) {
  return {
    schema: PUBLIC_RUN_STREAM_SCHEMA,
    event_id: "semantic-1",
    run_id: "run-1",
    stream_incarnation: 2,
    emitted_at: "2026-08-09T00:00:00Z",
    event_type: eventType,
    payload,
    ...overrides,
  };
}

test("adapts a schema-valid assistant delta and preserves transport fencing", () => {
  const adapted = adaptPublicRunStreamEventV3({
    eventHeader: "assistant_text_delta",
    transportCursor: "run-1:2:9-0",
    targetRunId: "run-1",
    targetStreamIncarnation: 2,
    value: envelope("assistant_text_delta", { delta: "hello" }),
  });

  assert.equal(adapted?.event, "message:chunk");
  assert.equal(adapted?.data.content, "hello");
  assert.equal(adapted?.data.event_id, "semantic-1");
  assert.equal(adapted?.streamIncarnation, 2);
});

test("rejects foreign runs, incarnations, cursors, headers, and extra fields", () => {
  const base = {
    eventHeader: "stream_open",
    transportCursor: "run-1:2:1-0",
    targetRunId: "run-1",
    targetStreamIncarnation: 2,
    value: envelope("stream_open", { design_id: STREAM_DESIGN_ID }),
  };

  assert.ok(adaptPublicRunStreamEventV3(base));
  assert.equal(
    adaptPublicRunStreamEventV3({ ...base, targetRunId: "run-2" }),
    null,
  );
  assert.equal(
    adaptPublicRunStreamEventV3({ ...base, targetStreamIncarnation: 3 }),
    null,
  );
  assert.equal(
    adaptPublicRunStreamEventV3({ ...base, transportCursor: "legacy-id" }),
    null,
  );
  assert.equal(
    adaptPublicRunStreamEventV3({ ...base, eventHeader: "reasoning.delta" }),
    null,
  );
  assert.equal(
    adaptPublicRunStreamEventV3({
      ...base,
      value: { ...base.value, tenant_scope: "must-not-be-public" },
    }),
    null,
  );
});

test("rejects schema bounds before adapting network input", () => {
  const assistant = {
    eventHeader: "assistant_text_delta",
    transportCursor: "run-1:2:1-0",
    targetRunId: "run-1",
    value: envelope("assistant_text_delta", { delta: "hello" }),
  };

  assert.equal(
    adaptPublicRunStreamEventV3({
      ...assistant,
      value: envelope("assistant_text_delta", { delta: "x".repeat(8193) }),
    }),
    null,
  );
  assert.equal(
    adaptPublicRunStreamEventV3({
      ...assistant,
      value: envelope("assistant_text_delta", { delta: "hello" }, {
        event_id: "x".repeat(257),
      }),
    }),
    null,
  );
  assert.equal(
    adaptPublicRunStreamEventV3({
      ...assistant,
      value: envelope("assistant_text_delta", { delta: "hello" }, {
        emitted_at: "not-a-date",
      }),
    }),
    null,
  );
  assert.equal(
    adaptPublicRunStreamEventV3({
      eventHeader: "stream_open",
      transportCursor: "invalid run:2:1-0",
      targetRunId: "invalid run",
      value: envelope("stream_open", { design_id: STREAM_DESIGN_ID }, {
        run_id: "invalid run",
      }),
    }),
    null,
  );
  assert.equal(
    adaptPublicRunStreamEventV3({
      eventHeader: "semantic_stage",
      transportCursor: "run-1:2:1-0",
      targetRunId: "run-1",
      value: envelope("semantic_stage", {
        event: "run_event",
        data: Object.fromEntries(
          Array.from({ length: 65 }, (_, index) => [`key_${index}`, index]),
        ),
      }),
    }),
    null,
  );
});

test("rejects hidden-reasoning and runtime-approval event names", () => {
  for (const eventType of ["reasoning.delta", "approval.required"]) {
    assert.equal(
      adaptPublicRunStreamEventV3({
        eventHeader: eventType,
        transportCursor: "run-1:2:1-0",
        targetRunId: "run-1",
        value: envelope(eventType, { delta: "private" }),
      }),
      null,
    );
  }
});
