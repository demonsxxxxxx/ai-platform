import assert from "node:assert/strict";
import test, { afterEach, beforeEach } from "node:test";
import { installSelectTestDom } from "../../../components/common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../../components/common/__tests__/lobeUiTestRoot";
import { act, createElement } from "react";

import { RunQuestionCard } from "../../../components/chat/RunQuestionCard.tsx";
import { RunInputHistory } from "../../../components/chat/RunInputHistory.tsx";
import { presentRunInputAnswer } from "../../../components/chat/runInputPresentation.ts";
import type {
  RunInputRecord,
  RunInputSubmissionRequest,
  RunInputsProjection,
  SessionRunInputsResponse,
} from "../../../services/api/session.ts";
import { sessionApi } from "../../../services/api/session.ts";
import { ApiRequestError } from "../../../services/api/fetch.ts";
import { useRunInputs } from "../runInputs.ts";
import type { RunInputsController } from "../types.ts";

const originalHistoryApi = sessionApi.getRunInputHistory;
beforeEach(() => {
  sessionApi.getRunInputHistory = async (sessionId) => ({
    session_id: sessionId, runs: [], has_more: false, next_before_run_id: null,
  });
});
afterEach(() => { sessionApi.getRunInputHistory = originalHistoryApi; });

function installDom() {
  const environment = installSelectTestDom();
  document.body.innerHTML = "<div id='root'></div>";
  return { dom: { window }, restore: environment.close };
}

function questionProjection(
  runId: string,
  options: { state?: RunInputsProjection["state"]; status?: "pending" | "answered" | "resolved" | "closed" } = {},
): RunInputsProjection {
  return {
    run_id: runId,
    state: options.state ?? "open",
    inputs: [],
    questions: [
      {
        question_id: "question-batch-1",
        status: options.status ?? "pending",
        created_at: "2026-10-07T00:00:00Z",
        questions: [
          {
            key: "q0",
            question: "Which formats should I include?",
            header: "Output",
            multiSelect: true,
            options: [
              { key: "o0", label: "Brief", description: "A short summary" },
              { key: "o1", label: "Detailed", description: "More explanation" },
            ],
          },
        ],
      },
    ],
  };
}

function answerRecord(body: Extract<RunInputSubmissionRequest, { question_id: string }>): RunInputRecord {
  return {
    input_id: body.input_id,
    kind: "answer",
    text: null,
    question_id: body.question_id,
    answers: body.answers,
    status: "applied",
    created_at: "2026-10-07T00:00:01Z",
  };
}

async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

test("restores Run questions after remount and submits multi-select answers to that Run", async () => {
  const env = installDom();
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  let root = await createLobeTestRoot(container);
  const originalGet = sessionApi.getRunInputs;
  const originalSubmit = sessionApi.submitRunInput;
  let persisted = questionProjection("run-question");
  const submissions: Array<{ runId: string; body: RunInputSubmissionRequest }> = [];
  sessionApi.getRunInputs = async (runId) => {
    assert.equal(runId, "run-question");
    return structuredClone(persisted);
  };
  sessionApi.submitRunInput = async (runId, body) => {
    submissions.push({ runId, body });
    if (!("question_id" in body)) throw new Error("expected_question_answer");
    persisted = {
      ...questionProjection(runId, { state: "inactive", status: "answered" }),
      inputs: [answerRecord(body)],
    };
    return { input_id: body.input_id, status: "applied" };
  };

  let latest: RunInputsController | null = null;
  let canSend = false;
  function Probe() {
    const controls = useRunInputs({
      sessionId: "session-question",
      runId: "run-question",
      isRunActive: persisted.state === "open",
    });
    latest = controls;
    const batch = controls.projection?.questions[0];
    return batch ? createElement(RunQuestionCard, { batch, runInputs: controls, canSend }) : null;
  }
  const controller = () => latest as RunInputsController;
  const render = async () => {
    await act(async () => {
      root.render(createElement(Probe));
      await flush();
    });
  };
  const restore = () => {
    sessionApi.getRunInputs = originalGet;
    sessionApi.submitRunInput = originalSubmit;
  };

  try {
    await render();
    assert.equal(controller().projection?.questions[0]?.status, "pending");
    assert.ok(container.querySelector("[data-run-question-card]"));
    const readOnlyForm = container.querySelector("[data-run-question-card] form");
    assert.ok(readOnlyForm);
    await act(async () => {
      readOnlyForm.dispatchEvent(new env.dom.window.Event("submit", { bubbles: true, cancelable: true }));
      await flush();
    });
    assert.equal(submissions.length, 0);
    assert.ok(container.querySelector<HTMLInputElement>('input[type="checkbox"]')?.disabled);
    canSend = true;
    await render();
    const checkboxes = container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
    assert.equal(checkboxes.length, 2);
    await act(async () => {
      checkboxes[0].click();
      checkboxes[1].click();
    });
    const form = container.querySelector("[data-run-question-card] form");
    assert.ok(form);
    await act(async () => {
      form.dispatchEvent(new env.dom.window.Event("submit", { bubbles: true, cancelable: true }));
      await flush();
    });

    assert.equal(submissions.length, 1);
    assert.equal(submissions[0].runId, "run-question");
    assert.ok("question_id" in submissions[0].body);
    assert.equal(submissions[0].body.question_id, "question-batch-1");
    assert.deepEqual(submissions[0].body.answers, {
      q0: ["o0", "o1"],
    });

    await act(async () => root.unmount());
    root = await createLobeTestRoot(container);
    await act(async () => {
      root.render(createElement(Probe));
      await flush();
    });
    assert.equal(controller().projection?.questions[0]?.status, "answered");
    assert.equal(container.querySelector('[data-run-question-status="closed"]') !== null, true);
    assert.match(container.textContent ?? "", /未完成处理/);
  } finally {
    await act(async () => root.unmount());
    restore();
    env.restore();
  }
});

test("retries uncertain text submissions with the same input ID to the same Run", async () => {
  const env = installDom();
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  const root = await createLobeTestRoot(container);
  const originalGet = sessionApi.getRunInputs;
  const originalSubmit = sessionApi.submitRunInput;
  const persisted: RunInputsProjection = {
    run_id: "run-text",
    state: "open",
    inputs: [],
    questions: [],
  };
  const submissions: Array<{ runId: string; body: RunInputSubmissionRequest }> = [];
  sessionApi.getRunInputs = async () => structuredClone(persisted);
  sessionApi.submitRunInput = async (runId, body) => {
    submissions.push({ runId, body });
    if (submissions.length === 1) throw new Error("response_lost");
    if (!("text" in body)) throw new Error("expected_text_input");
    persisted.inputs.push({
      input_id: body.input_id,
      kind: "text",
      text: body.text,
      question_id: null,
      answers: null,
      status: "queued",
      created_at: "2026-10-07T00:00:01Z",
    });
    return { input_id: body.input_id, status: "queued" };
  };

  let latest: RunInputsController | null = null;
  function Probe() {
    latest = useRunInputs({
      sessionId: "session-text",
      runId: "run-text",
      isRunActive: true,
    });
    return null;
  }
  const controls = () => latest as RunInputsController;
  const restore = () => {
    sessionApi.getRunInputs = originalGet;
    sessionApi.submitRunInput = originalSubmit;
  };

  try {
    await act(async () => {
      root.render(createElement(Probe));
      await flush();
    });
    assert.equal(controls().projection?.run_id, "run-text");
    let accepted = true;
    await act(async () => {
      accepted = await controls().submitText("please add one example");
      await flush();
    });
    assert.equal(accepted, false);
    assert.equal(controls().pendingSubmission?.state, "uncertain");
    assert.equal(submissions.length, 1);

    await act(async () => {
      accepted = await controls().retryPendingSubmission();
      await flush();
    });
    assert.equal(accepted, true);
    assert.equal(submissions.length, 2);
    assert.equal(submissions[0].runId, "run-text");
    assert.equal(submissions[1].runId, "run-text");
    assert.deepEqual(submissions[1].body, submissions[0].body);
    assert.ok("text" in submissions[1].body);
    assert.equal(submissions[1].body.text, "please add one example");
    assert.equal(persisted.inputs[0]?.input_id, submissions[0].body.input_id);
    assert.equal(controls().pendingSubmission, null);
  } finally {
    await act(async () => root.unmount());
    restore();
    env.restore();
  }
});

test("ignores late reads and writes owned by a previous session", async () => {
  const env = installDom();
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  const root = await createLobeTestRoot(container);
  const originalGet = sessionApi.getRunInputs;
  const originalSubmit = sessionApi.submitRunInput;
  let resolveOldRead!: (value: RunInputsProjection) => void;
  const oldRead = new Promise<RunInputsProjection>((resolve) => {
    resolveOldRead = resolve;
  });
  let resolveOldWrite!: (value: { input_id: string; status: "queued" }) => void;
  let oldWrite: Promise<{ input_id: string; status: "queued" }> | null = null;
  let oldWriteBody: RunInputSubmissionRequest | null = null;
  sessionApi.getRunInputs = async (runId) => {
    if (runId === "run-old") return oldRead;
    return { run_id: runId, state: "open", inputs: [], questions: [] };
  };
  sessionApi.submitRunInput = async (runId, body) => {
    assert.equal(runId, "run-new");
    oldWriteBody = body;
    oldWrite = new Promise((resolve) => {
      resolveOldWrite = resolve;
    });
    return oldWrite;
  };

  let latest: RunInputsController | null = null;
  function Probe({ sessionId, runId }: { sessionId: string; runId: string }) {
    latest = useRunInputs({ sessionId, runId, isRunActive: true });
    return null;
  }
  const controller = () => latest as RunInputsController;
  const render = async (sessionId: string, runId: string) => {
    await act(async () => {
      root.render(createElement(Probe, { sessionId, runId }));
      await flush();
    });
  };

  try {
    await render("session-old", "run-old");
    await render("session-new", "run-new");
    assert.equal(controller().projection?.run_id, "run-new");
    resolveOldRead({ run_id: "run-old", state: "open", inputs: [], questions: [] });
    await act(async () => flush());
    assert.equal(controller().projection?.run_id, "run-new");

    let pendingWrite: Promise<boolean> | null = null;
    await act(async () => {
      pendingWrite = controller().submitText("late input");
      await flush();
    });
    assert.ok(pendingWrite);
    assert.ok(oldWrite);
    assert.ok(oldWriteBody && "input_id" in oldWriteBody);
    await render("session-next", "run-next");
    assert.equal(controller().projection?.run_id, "run-next");
    const submittedBody = oldWriteBody as RunInputSubmissionRequest | null;
    assert.ok(submittedBody && "input_id" in submittedBody);
    resolveOldWrite({ input_id: submittedBody.input_id, status: "queued" });
    await act(async () => {
      await oldWrite;
      await pendingWrite;
      await flush();
    });
    assert.equal(controller().projection?.run_id, "run-next");
    assert.deepEqual(controller().projection?.inputs, []);
  } finally {
    await act(async () => root.unmount());
    sessionApi.getRunInputs = originalGet;
    sessionApi.submitRunInput = originalSubmit;
    env.restore();
  }
});

test("retries failed final reads and can refresh stale input history after completion", async () => {
  const env = installDom();
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  const root = await createLobeTestRoot(container);
  const originalGet = sessionApi.getRunInputs;
  let active = true;
  let offline = false;
  let reads = 0;
  let latest: RunInputsController | null = null;
  sessionApi.getRunInputs = async () => {
    reads += 1;
    if (offline) throw new Error("temporarily_offline");
    return { ...questionProjection("run-final"), state: active ? "open" : "inactive" };
  };
  function Probe() {
    latest = useRunInputs({ sessionId: "session-final", runId: "run-final", isRunActive: active });
    return null;
  }
  const controls = () => latest as RunInputsController;
  try {
    await act(async () => {
      root.render(createElement(Probe));
      await flush();
    });
    assert.equal(controls().isClosed, false);
    active = false;
    offline = true;
    await act(async () => {
      root.render(createElement(Probe));
      await flush();
    });
    assert.equal(controls().isClosed, true);
    assert.equal(controls().loadFailed, true);
    assert.equal(controls().projection?.state, "open");
    await act(async () => new Promise((resolve) => setTimeout(resolve, 2_100)));
    assert.equal(reads, 4);
    offline = false;
    await act(async () => { await controls().refresh(); });
    assert.equal(controls().loadFailed, false);
    assert.equal(controls().projection?.state, "inactive");
    assert.equal(controls().isClosed, true);
  } finally {
    await act(async () => root.unmount());
    sessionApi.getRunInputs = originalGet;
    env.restore();
  }
});

test("answers indistinguishable redacted options by key and distinguishes free text from option keys", async () => {
  const env = installDom();
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  const root = await createLobeTestRoot(container);
  const originalGet = sessionApi.getRunInputs;
  const originalSubmit = sessionApi.submitRunInput;
  const projection = questionProjection("run-private");
  projection.questions[0].questions[0].options.forEach((option) => { option.label = "[redacted-email]"; });
  const bodies: RunInputSubmissionRequest[] = [];
  sessionApi.getRunInputs = async () => structuredClone(projection);
  sessionApi.submitRunInput = async (_runId, body) => {
    bodies.push(body);
    return { input_id: body.input_id, status: "applied" };
  };
  let latest: RunInputsController | null = null;
  function Probe() {
    latest = useRunInputs({ sessionId: "session-private", runId: "run-private", isRunActive: true });
    const controls = latest;
    return controls.projection ? createElement(RunQuestionCard, { batch: controls.projection.questions[0], runInputs: controls, canSend: true }) : null;
  }
  const controls = () => latest as RunInputsController;
  try {
    await act(async () => { root.render(createElement(Probe)); await flush(); });
    const boxes = container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
    assert.match(boxes[0].closest("label")?.textContent ?? "", /1\. \[redacted-email\]/);
    assert.match(boxes[1].closest("label")?.textContent ?? "", /2\. \[redacted-email\]/);
    await act(async () => { boxes[0].click(); boxes[1].click(); });
    assert.equal(boxes[0].checked, true);
    assert.equal(boxes[1].checked, true);
    const form = container.querySelector("form");
    assert.ok(form);
    await act(async () => { form.dispatchEvent(new env.dom.window.Event("submit", { bubbles: true, cancelable: true })); await flush(); });
    assert.deepEqual(bodies[0], { input_id: bodies[0].input_id, question_id: "question-batch-1", answers: { q0: ["o0", "o1"] } });
    await act(async () => { assert.equal(await controls().submitAnswers("question-batch-1", { q0: { text: "o0" } }), true); });
    assert.ok("answers" in bodies[1]);
    assert.deepEqual(bodies[1].answers, { q0: { text: "o0" } });
    const batch = projection.questions[0];
    assert.deepEqual(presentRunInputAnswer("q0", ["o0", "o1"], batch), { question: "Which formats should I include?", answer: "1. [redacted-email]、2. [redacted-email]" });
    assert.deepEqual(presentRunInputAnswer("q0", { text: "o0" }, batch), { question: "Which formats should I include?", answer: "o0" });
    assert.equal(presentRunInputAnswer("q0", "unknown-internal-key", batch).answer, null);
  } finally {
    await act(async () => root.unmount());
    sessionApi.getRunInputs = originalGet;
    sessionApi.submitRunInput = originalSubmit;
    env.restore();
  }
});

test("labels a server-selected timeout answer and keeps the current Run open", async () => {
  const env = installDom();
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  const root = await createLobeTestRoot(container);
  const originalGet = sessionApi.getRunInputs;
  const projection = questionProjection("run-auto", { status: "resolved" });
  projection.inputs = [{
    input_id: "auto-answer", kind: "answer", text: null, question_id: "question-batch-1",
    answers: { q0: ["o0"] }, status: "applied", source: "timeout_default",
    created_at: "2026-10-07T00:05:01Z",
  }];
  sessionApi.getRunInputs = async () => structuredClone(projection);
  let latest: RunInputsController | null = null;
  function Probe() {
    latest = useRunInputs({ sessionId: "session-auto", runId: "run-auto", isRunActive: true });
    return latest.projection ? createElement(RunInputHistory, { runInputs: latest, canSend: true }) : null;
  }
  try {
    await act(async () => { root.render(createElement(Probe)); await flush(); });
    assert.equal((latest as unknown as RunInputsController).isClosed, false);
    assert.match(container.textContent ?? "", /系统默认答复/);
    assert.match(container.textContent ?? "", /Brief/);
    assert.match(container.textContent ?? "", /已处理/);
    assert.doesNotMatch(container.textContent ?? "", /排队中/);
  } finally {
    await act(async () => root.unmount());
    sessionApi.getRunInputs = originalGet;
    env.restore();
  }
});

for (const response of ["conflict", "uncertain"] as const) {
  test(`reconciles ${response} manual answers with a server timeout answer`, async () => {
    const env = installDom();
    const container = env.dom.window.document.getElementById("root");
    assert.ok(container);
    const root = await createLobeTestRoot(container);
    const originalGet = sessionApi.getRunInputs;
    const originalSubmit = sessionApi.submitRunInput;
    let persisted = questionProjection("run-race");
    let writes = 0;
    sessionApi.getRunInputs = async () => structuredClone(persisted);
    sessionApi.submitRunInput = async () => {
      writes += 1;
      persisted = questionProjection("run-race", { status: "resolved" });
      persisted.inputs = [{ input_id: "server-default", kind: "answer", text: null,
        question_id: "question-batch-1", answers: { q0: ["o0"] }, status: "applied",
        source: "timeout_default", created_at: "2026-10-07T00:05:01Z" }];
      if (response === "conflict") throw new ApiRequestError("Already answered", 409, "run_input_question_already_answered");
      throw new Error("response lost");
    };
    let latest: RunInputsController | null = null;
    function Probe() {
      latest = useRunInputs({ sessionId: "session-race", runId: "run-race", isRunActive: true });
      return createElement(RunInputHistory, { runInputs: latest, canSend: true });
    }
    const controls = () => latest as unknown as RunInputsController;
    try {
      await act(async () => { root.render(createElement(Probe)); await flush(); });
      await act(async () => { assert.equal(await controls().submitAnswers("question-batch-1", { q0: ["o1"] }), false); await flush(); });
      assert.equal(controls().isClosed, false);
      assert.equal(controls().pendingSubmission, null);
      assert.equal(controls().submissionError, null);
      assert.equal(controls().lastAcceptedSubmission, null, "server choice must not be reported as accepted manual input");
      assert.match(container.textContent ?? "", /系统默认答复/);
      assert.equal(await controls().retryPendingSubmission(), false);
      assert.equal(writes, 1);
    } finally {
      await act(async () => root.unmount());
      sessionApi.getRunInputs = originalGet;
      sessionApi.submitRunInput = originalSubmit;
      env.restore();
    }
  });
}

for (const noOptions of [false, true]) {
  test(`shows server deadline policy and default choice with noOptions=${noOptions}`, async () => {
    const env = installDom();
    const container = env.dom.window.document.getElementById("root");
    assert.ok(container);
    const root = await createLobeTestRoot(container);
    const originalGet = sessionApi.getRunInputs;
    const originalSubmit = sessionApi.submitRunInput;
    const projection = questionProjection("run-deadline");
    projection.questions[0].deadline_at = "2026-10-07T00:05:00Z";
    projection.questions[0].default_answers = noOptions ? null : { q0: ["o1"] };
    if (noOptions) projection.questions[0].questions[0].options = [];
    let writes = 0;
    sessionApi.getRunInputs = async () => structuredClone(projection);
    sessionApi.submitRunInput = async () => { writes += 1; throw new Error("unexpected client default"); };
    function Probe() {
      const controls = useRunInputs({ sessionId: "session-deadline", runId: "run-deadline", isRunActive: true });
      return createElement(RunInputHistory, { runInputs: controls, canSend: true });
    }
    try {
      await act(async () => { root.render(createElement(Probe)); await flush(); });
      assert.match(container.textContent ?? "", /5分钟未提交/);
      assert.doesNotMatch(container.textContent ?? "", /q0|o1/);
      assert.equal(writes, 0, "browser must not submit a default even when the timestamp is past");
      if (noOptions) {
        assert.equal(container.querySelectorAll("input").length, 0);
        assert.ok(container.querySelector("textarea[aria-label]"));
        assert.equal(container.querySelector("[data-run-question-default]"), null);
      } else {
        assert.match(container.querySelector("[data-run-question-default]")?.textContent ?? "", /Detailed/);
      }
    } finally {
      await act(async () => root.unmount());
      sessionApi.getRunInputs = originalGet;
      sessionApi.submitRunInput = originalSubmit;
      env.restore();
    }
  });
}

for (const scenario of [
  { name: "accepted answer", state: "open", status: "applied", questionStatus: "answered", visible: false },
  { name: "processed history", state: "inactive", status: "applied", questionStatus: "resolved", visible: false },
  { name: "queued input", state: "open", status: "queued", questionStatus: "answered", visible: true },
  { name: "closed input", state: "inactive", status: "closed", questionStatus: "closed", visible: true },
  { name: "unprocessed answer", state: "inactive", status: "applied", questionStatus: "answered", visible: true },
] as const) {
  test(`task input echoes hide successful receipts and preserve ${scenario.name}`, async () => {
    const env = installDom();
    const container = env.dom.window.document.getElementById("root");
    assert.ok(container);
    const root = await createLobeTestRoot(container);
    const originalGet = sessionApi.getRunInputs;
    const projection = questionProjection("run-echo", { state: scenario.state, status: scenario.questionStatus });
    projection.inputs = [
      { input_id: "text-echo", kind: "text", text: "synthetic continuation", question_id: null, answers: null, status: scenario.status, created_at: "2026-10-07T00:00:01Z" },
      { input_id: "answer-echo", kind: "answer", text: null, question_id: "question-batch-1", answers: { q0: ["o0"] }, status: scenario.status, created_at: "2026-10-07T00:00:02Z" },
    ];
    sessionApi.getRunInputs = async () => structuredClone(projection);
    let latest: RunInputsController | null = null;
    function Probe() {
      latest = useRunInputs({ sessionId: "session-echo", runId: projection.run_id, isRunActive: projection.state === "open" });
      return createElement(RunInputHistory, { runInputs: latest, canSend: true });
    }
    try {
      await act(async () => { root.render(createElement(Probe)); await flush(); });
      const controls = latest as unknown as RunInputsController;
      assert.equal(controls.history[0].inputs.length, 2, "hiding an echo must not discard the Run input projection");
      assert.equal(Boolean(container.querySelector("[data-run-input-entry]")), scenario.visible);
      assert.equal(Boolean(container.querySelector("[data-run-input-history]")), scenario.visible);
      assert.doesNotMatch(container.textContent ?? "", /任务已接收/);
      if (scenario.visible) assert.match(container.textContent ?? "", scenario.status === "queued" ? /排队中/ : /未处理/);
    } finally {
      await act(async () => root.unmount());
      sessionApi.getRunInputs = originalGet;
      env.restore();
    }
  });
}

function completedProjection(runId: string, text: string): RunInputsProjection {
  const projection = questionProjection(runId, { state: "inactive", status: "resolved" });
  projection.inputs = [
    { input_id: `${runId}-text`, kind: "text", text, question_id: null, answers: null, status: "applied", created_at: "2026-10-07T00:00:01Z" },
    { input_id: `${runId}-answer`, kind: "answer", text: null, question_id: "question-batch-1", answers: { q0: ["o0", "o1"] }, status: "applied", created_at: "2026-10-07T00:00:02Z" },
  ];
  return projection;
}

test("retains completed Run input history across pagination and remount without echoing it in the composer", async () => {
  const env = installDom();
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  let root = await createLobeTestRoot(container);
  const originalGet = sessionApi.getRunInputs;
  let runId = "run-current";
  const previous = completedProjection("run-previous", "previous continuation");
  const older = completedProjection("run-older", "older continuation");
  const current = completedProjection("run-current", "current continuation");
  const requested: Array<string | undefined> = [];
  sessionApi.getRunInputs = async (id) => id === "run-current" ? structuredClone(current) : questionProjection(id);
  sessionApi.getRunInputHistory = async (sessionId, options = {}) => {
    assert.equal(sessionId, "session-history");
    requested.push(options.beforeRunId);
    if (options.beforeRunId) return { session_id: sessionId, runs: [older], has_more: false, next_before_run_id: null };
    return {
      session_id: sessionId, runs: runId === "run-current" ? [current, previous] : [questionProjection(runId), current, previous],
      has_more: true, next_before_run_id: "run-previous",
    };
  };
  let latest: RunInputsController | null = null;
  function Probe() {
    latest = useRunInputs({ sessionId: "session-history", runId, isRunActive: runId !== "run-current", identityKey: "tenant-a:user-a" });
    return createElement(RunInputHistory, { runInputs: latest, canSend: true });
  }
  const controls = () => latest as RunInputsController;
  const historyText = () => controls().history.flatMap((run) => run.inputs).map((input) => input.text ?? "").join(" ");
  const render = async () => { await act(async () => { root.render(createElement(Probe)); await flush(); }); };
  try {
    await render();
    assert.match(historyText(), /previous continuation/);
    assert.match(historyText(), /current continuation/);
    assert.doesNotMatch(container.textContent ?? "", /previous continuation|current continuation|Brief、Detailed/);
    assert.doesNotMatch(container.textContent ?? "", /q0|o0|o1/);
    assert.equal(container.querySelector('[data-run-input-history-run="run-previous"]'), null);
    await act(async () => { assert.equal(await controls().loadMoreHistory(), true); });
    assert.equal(requested.at(-1), "run-previous");
    assert.match(historyText(), /older continuation/);
    assert.equal(controls().historyHasMore, false);
    assert.equal(container.querySelector("[data-run-input-history]"), null, "processed receipts must not leave an empty task-input area");
    runId = "run-next";
    await render();
    assert.match(historyText(), /older continuation/);
    assert.match(historyText(), /current continuation/);
    assert.doesNotMatch(container.textContent ?? "", /older continuation|current continuation/);
    assert.equal(controls().history.filter((run) => run.run_id === "run-next").length, 1);
    assert.equal(container.querySelector('[data-run-input-history-run="run-current"] input'), null);
    assert.equal(container.querySelector('[data-run-input-history-run="run-next"] input')?.hasAttribute("disabled"), false);
    assert.equal(container.querySelector("[data-run-input-history-run]")?.getAttribute("data-run-input-history-run"), "run-next");
    const historyRegion = container.querySelector<HTMLDivElement>("[data-run-input-history]");
    assert.ok(historyRegion);
    assert.equal(historyRegion.scrollTop, 0, "a new pending question must be visible without processed receipts");
    await act(async () => root.unmount());
    root = await createLobeTestRoot(container);
    await render();
    assert.match(historyText(), /current continuation/);
    assert.match(historyText(), /previous continuation/);
    await act(async () => { await controls().loadMoreHistory(); });
    assert.match(historyText(), /older continuation/);
    assert.doesNotMatch(container.textContent ?? "", /older continuation|current continuation|previous continuation/);
  } finally {
    await act(async () => root.unmount());
    sessionApi.getRunInputs = originalGet;
    env.restore();
  }
});

test("refresh traverses a disjoint history gap in order even when its first page overlaps a live projection", async () => {
  const env = installDom();
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  const root = await createLobeTestRoot(container);
  const originalGet = sessionApi.getRunInputs;
  let refreshed = false;
  const ids = (values: string[]) => values.map((id) => completedProjection(id, `${id} continuation`));
  const requested: Array<string | undefined> = [];
  sessionApi.getRunInputs = async () => questionProjection("live");
  sessionApi.getRunInputHistory = async (sessionId, options = {}) => {
    requested.push(options.beforeRunId);
    if (!refreshed) return { session_id: sessionId, runs: ids(["old2", "old1"]), has_more: true, next_before_run_id: "old1" };
    if (!options.beforeRunId) return { session_id: sessionId, runs: [questionProjection("live"), ...ids(["new2"])], has_more: true, next_before_run_id: "new2" };
    if (options.beforeRunId === "new2") return { session_id: sessionId, runs: ids(["mid2", "mid1"]), has_more: true, next_before_run_id: "mid1" };
    assert.equal(options.beforeRunId, "mid1");
    return { session_id: sessionId, runs: ids(["old2", "old1"]), has_more: false, next_before_run_id: null };
  };
  let latest: RunInputsController | null = null;
  function Probe() {
    latest = useRunInputs({ sessionId: "session-gap", runId: "live", isRunActive: true, identityKey: "tenant:user" });
    return createElement(RunInputHistory, { runInputs: latest, canSend: true });
  }
  const controls = () => latest as RunInputsController;
  try {
    await act(async () => { root.render(createElement(Probe)); await flush(); });
    assert.deepEqual(controls().history.map((run) => run.run_id), ["live", "old2", "old1"]);
    refreshed = true;
    await act(async () => { assert.equal(await controls().refreshHistory(), true); });
    await act(async () => { assert.equal(await controls().loadMoreHistory(), true); });
    assert.equal(requested.at(-1), "new2", "fresh-page cursor must replace the disjoint cached cursor");
    assert.deepEqual(controls().history.map((run) => run.run_id), ["live", "new2", "mid2", "mid1", "old2", "old1"]);
    await act(async () => { assert.equal(await controls().loadMoreHistory(), true); });
    assert.equal(requested.at(-1), "mid1");
    assert.deepEqual(controls().history.map((run) => run.run_id), ["live", "new2", "mid2", "mid1", "old2", "old1"]);
    assert.equal(controls().historyHasMore, false);
  } finally {
    await act(async () => root.unmount());
    sessionApi.getRunInputs = originalGet;
    env.restore();
  }
});

test("drops delayed history pages on session or auth identity changes and preserves history after read failure", async () => {
  const env = installDom();
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  const root = await createLobeTestRoot(container);
  const originalGet = sessionApi.getRunInputs;
  let sessionId = "session-old";
  let identityKey = "tenant-old:user-old";
  let fail = false;
  let resolveOld!: (value: SessionRunInputsResponse) => void;
  const old = new Promise<SessionRunInputsResponse>((resolve) => { resolveOld = resolve; });
  let resolveAuth!: (value: SessionRunInputsResponse) => void;
  const oldAuth = new Promise<SessionRunInputsResponse>((resolve) => { resolveAuth = resolve; });
  sessionApi.getRunInputs = async () => questionProjection("run-shared");
  sessionApi.getRunInputHistory = async (id) => {
    if (id === "session-old") return old;
    if (identityKey === "tenant-old:user-old") return oldAuth;
    if (fail) throw new Error("offline");
    return { session_id: id, runs: [completedProjection("run-safe", "safe continuation")], has_more: false, next_before_run_id: null };
  };
  let latest: RunInputsController | null = null;
  function Probe() {
    latest = useRunInputs({ sessionId, identityKey, runId: "run-shared", isRunActive: false });
    return createElement(RunInputHistory, { runInputs: latest, canSend: true });
  }
  const controls = () => latest as RunInputsController;
  const render = async () => { await act(async () => { root.render(createElement(Probe)); await flush(); }); };
  try {
    await render();
    sessionId = "session-new";
    await render();
    identityKey = "tenant-new:user-new";
    await render();
    resolveOld({ session_id: "session-old", runs: [completedProjection("run-private-old", "old private continuation")], has_more: false, next_before_run_id: null });
    resolveAuth({ session_id: "session-new", runs: [completedProjection("run-private-auth", "auth private continuation")], has_more: false, next_before_run_id: null });
    await act(async () => flush());
    assert.equal(controls().history.some((run) => run.run_id === "run-safe"), true);
    assert.equal(controls().history.some((run) => run.run_id === "run-private-old" || run.run_id === "run-private-auth"), false);
    assert.doesNotMatch(container.textContent ?? "", /safe continuation|private continuation/);
    fail = true;
    await act(async () => { assert.equal(await controls().refreshHistory(), false); });
    assert.equal(controls().historyLoadFailed, true);
    assert.equal(controls().history.some((run) => run.run_id === "run-safe"), true);
    assert.doesNotMatch(container.textContent ?? "", /safe continuation/);
    assert.ok(container.querySelector("[data-run-input-history-failure]"));
    await act(async () => { controls().retire(); });
    assert.equal(controls().history.length, 0);
    assert.doesNotMatch(container.textContent ?? "", /safe continuation/);
  } finally {
    await act(async () => root.unmount());
    sessionApi.getRunInputs = originalGet;
    env.restore();
  }
});

for (const outcome of ["accepted", "uncertain", "late-success", "closed", "read-only", "other-batch", "navigate", "identity", "resolved", "double-click", "retry-navigation", "retry-retire", "retry-unmount", "retry-cancelled-run", "timeout-before-retry", "timeout-during-retry-success", "timeout-during-retry-conflict", "timeout-during-retry-uncertain"] as const) {
  test(`uncertain question retry preserves ownership and original answers: ${outcome}`, async () => {
    const env = installDom();
    const container = env.dom.window.document.getElementById("root");
    assert.ok(container);
    const root = await createLobeTestRoot(container);
    let mounted = true;
    const originalGet = sessionApi.getRunInputs;
    const originalSubmit = sessionApi.submitRunInput;
    const persisted = questionProjection("run-question-retry");
    let runId = persisted.run_id;
    let canSend = true;
    let isRunActive = true;
    let otherBatch = false;
    let identityKey = "synthetic-owner";
    const submissions: RunInputSubmissionRequest[] = [];
    const writeSignals: Array<AbortSignal | undefined> = [];
    let resolveRetry!: () => void;
    const retryResponse = new Promise<void>((resolve) => { resolveRetry = resolve; });
    sessionApi.getRunInputs = async (id) => ({ ...structuredClone(persisted), run_id: id });
    sessionApi.submitRunInput = async (_id, body, options = {}) => {
      writeSignals.push(options.signal);
      submissions.push(structuredClone(body));
      if (!("question_id" in body)) throw new Error("expected_answer");
      if (submissions.length === 1) throw new Error("synthetic_transport_uncertainty");
      await retryResponse;
      if (outcome === "uncertain" || outcome === "timeout-during-retry-uncertain") throw new Error("synthetic_transport_uncertainty");
      if (outcome === "timeout-during-retry-conflict") throw new ApiRequestError("Already answered", 409, "run_input_question_already_answered");
      persisted.inputs.push(answerRecord(body));
      return { input_id: body.input_id, status: "applied" };
    };
    let latest!: RunInputsController;
    function Probe() {
      latest = useRunInputs({ sessionId: "session-question-retry", runId, isRunActive, identityKey });
      const batch = latest.projection?.questions[0];
      return batch ? createElement(RunQuestionCard, {
        batch: otherBatch ? { ...batch, question_id: "other-question" } : batch,
        runInputs: latest, canSend,
      }) : null;
    }
    const render = async () => { await act(async () => { root.render(createElement(Probe)); await flush(); }); };
    const retryButton = () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "重试提交答复");
    try {
      await render();
      await act(async () => { container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click(); });
      await act(async () => { container.querySelector("form")!.dispatchEvent(new env.dom.window.Event("submit", { bubbles: true, cancelable: true })); await flush(); });
      assert.equal(latest.pendingSubmission?.state, "uncertain");
      assert.equal(submissions.length, 1);
      assert.match(container.querySelector('[role="alert"]')?.textContent ?? "", /尚未确认答案是否提交成功/);
      assert.ok(retryButton());
      assert.equal(container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked, true);
      assert.equal(container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.disabled, true);
      assert.equal(container.querySelector<HTMLTextAreaElement>("textarea")!.disabled, true);
      assert.equal(container.querySelector<HTMLButtonElement>('button[type="submit"]')!.disabled, true);
      const applyTimeout = async () => {
        persisted.questions[0].status = "resolved";
        persisted.inputs = [{
          input_id: "server-timeout-answer", kind: "answer", text: null,
          question_id: "question-batch-1", answers: { q0: ["o1"] },
          status: "applied", source: "timeout_default", created_at: "2026-10-07T00:05:01Z",
        }];
        await act(async () => { await latest.refresh(); });
        assert.equal(latest.pendingSubmission, null);
        assert.equal(latest.submissionError, null);
        assert.equal(latest.lastAcceptedSubmission, null);
        assert.equal(latest.isClosed, false);
        assert.equal(retryButton(), undefined);
        assert.match(container.textContent ?? "", /系统已自动选择默认答复/);
      };
      if (outcome === "timeout-before-retry") {
        const staleRetry = latest.retryPendingSubmission;
        await applyTimeout();
        assert.equal(await staleRetry(), false);
        assert.equal(submissions.length, 1);
      } else if (outcome === "late-success") {
        const body = submissions[0];
        assert.ok("question_id" in body);
        persisted.inputs.push(answerRecord(body));
        await act(async () => { await latest.refresh(); });
        assert.equal(latest.pendingSubmission, null);
        assert.equal(retryButton(), undefined);
        assert.equal(container.querySelector("form"), null);
        assert.equal(submissions.length, 1);
      } else if (["closed", "read-only", "other-batch", "navigate", "identity", "resolved"].includes(outcome)) {
        if (outcome === "closed" || outcome === "resolved") {
          if (outcome === "closed") persisted.state = "inactive";
          else persisted.questions[0].status = "resolved";
          await act(async () => { await latest.refresh(); });
        } else {
          if (outcome === "read-only") canSend = false;
          if (outcome === "other-batch") otherBatch = true;
          if (outcome === "navigate") runId = "run-replacement";
          if (outcome === "identity") identityKey = "replacement-owner";
          await render();
        }
        assert.equal(retryButton(), undefined);
        assert.equal(container.querySelector('[role="alert"]'), null);
        assert.equal(submissions.length, 1);
      } else {
        const retry = retryButton()!;
        await act(async () => {
          retry.click();
          if (outcome === "double-click") retry.click();
          await flush();
        });
        assert.equal(submissions.length, 2, "a retry already in flight must not be resubmitted");
        assert.deepEqual(submissions[1], submissions[0]);
        assert.equal(latest.pendingSubmission?.state, "submitting");
        assert.equal(retryButton(), undefined);
        assert.equal(container.querySelector<HTMLButtonElement>('button[type="submit"]')!.disabled, true);
        if (outcome.startsWith("timeout-during-retry-")) {
          await applyTimeout();
          await act(async () => { resolveRetry(); await flush(); });
          assert.equal(latest.pendingSubmission, null);
          assert.equal(latest.submissionError, null);
          assert.equal(latest.lastAcceptedSubmission, null, "late manual result must not replace the server default");
          assert.deepEqual(latest.projection?.inputs.map((input) => input.input_id), ["server-timeout-answer"]);
          assert.equal(latest.isClosed, false);
          assert.equal(retryButton(), undefined);
          assert.equal(await latest.retryPendingSubmission(), false);
          assert.equal(submissions.length, 2);
          return;
        }
        if (outcome === "retry-cancelled-run") {
          // stopGeneration converges through authoritative terminal state;
          // a cancellation acknowledgement alone does not close the Run.
          isRunActive = false;
          persisted.state = "inactive";
          await render();
          assert.equal(latest.isClosed, true);
          assert.equal(retryButton(), undefined);
          await act(async () => { resolveRetry(); await flush(); });
          assert.equal(latest.isClosed, true);
          assert.equal(container.querySelector("form"), null);
          assert.equal(retryButton(), undefined);
          assert.equal(submissions.length, 2);
          return;
        }
        if (outcome === "retry-navigation" || outcome === "retry-retire" || outcome === "retry-unmount") {
          if (outcome === "retry-navigation") {
            runId = "run-replacement";
            await render();
          } else if (outcome === "retry-retire") {
            await act(async () => { latest.retire(); });
          } else {
            await act(async () => { root.unmount(); });
            mounted = false;
          }
          assert.equal(writeSignals[1]?.aborted, true, "retired owner must abort its retry transport");
          const retiredView = latest;
          await act(async () => { resolveRetry(); await flush(); });
          assert.equal(latest, retiredView, "late retry success must not render into a retired owner");
          assert.equal(submissions.length, 2);
          if (outcome === "retry-navigation") {
            assert.equal(latest.runId, "run-replacement");
            assert.deepEqual(latest.projection?.inputs, []);
            assert.equal(latest.pendingSubmission, null);
            assert.equal(latest.lastAcceptedSubmission, null);
          } else if (outcome === "retry-retire") {
            assert.equal(latest.isClosed, true);
            assert.equal(latest.projection, null);
            assert.equal(latest.pendingSubmission, null);
            assert.equal(latest.lastAcceptedSubmission, null);
          } else {
            assert.equal(container.childElementCount, 0);
          }
          return;
        }
        await act(async () => { resolveRetry(); await flush(); });
        if (outcome === "uncertain") {
          assert.equal(latest.pendingSubmission?.state, "uncertain");
          assert.ok(retryButton());
          assert.equal(container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked, true);
          assert.equal(container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.disabled, true);
          await act(async () => { retryButton()!.click(); await flush(); });
          assert.equal(submissions.length, 3);
          assert.deepEqual(submissions[2], submissions[0]);
        } else {
          assert.equal(latest.pendingSubmission, null);
          assert.equal(retryButton(), undefined);
          assert.equal(container.querySelector("form"), null);
        }
      }
    } finally {
      resolveRetry();
      await act(async () => { await flush(); if (mounted) root.unmount(); });
      sessionApi.getRunInputs = originalGet;
      sessionApi.submitRunInput = originalSubmit;
      env.restore();
    }
  });
}
