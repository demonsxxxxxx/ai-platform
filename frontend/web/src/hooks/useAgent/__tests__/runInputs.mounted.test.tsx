import assert from "node:assert/strict";
import test, { afterEach, beforeEach } from "node:test";
// jsdom 26 is the pinned mounted-test runtime and does not ship declarations.
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
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
  const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
    url: "http://localhost/",
    pretendToBeVisual: true,
  });
  const window = dom.window;
  const values: Record<string, unknown> = {
    window,
    document: window.document,
    navigator: window.navigator,
    HTMLElement: window.HTMLElement,
    Element: window.Element,
    Node: window.Node,
    HTMLInputElement: window.HTMLInputElement,
    HTMLTextAreaElement: window.HTMLTextAreaElement,
    Event: window.Event,
    InputEvent: window.InputEvent,
    CustomEvent: window.CustomEvent,
    localStorage: window.localStorage,
    sessionStorage: window.sessionStorage,
    indexedDB: globalThis.indexedDB,
    requestAnimationFrame: (callback: FrameRequestCallback) =>
      window.setTimeout(() => callback(Date.now()), 0),
    cancelAnimationFrame: (handle: number) => window.clearTimeout(handle),
    IS_REACT_ACT_ENVIRONMENT: true,
  };
  const previous = new Map(
    Object.keys(values).map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]),
  );
  for (const [key, value] of Object.entries(values)) {
    Object.defineProperty(globalThis, key, {
      configurable: true,
      writable: true,
      value,
    });
  }
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: () => ({
      matches: false,
      media: "",
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    }),
  });
  return {
    dom,
    restore() {
      for (const [key, descriptor] of previous) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor);
        else Reflect.deleteProperty(globalThis, key);
      }
      dom.window.close();
    },
  };
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
  const { createRoot } = await import("react-dom/client");
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  let root = createRoot(container);
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
    root = createRoot(container);
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
  const { createRoot } = await import("react-dom/client");
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  const root = createRoot(container);
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
  const { createRoot } = await import("react-dom/client");
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  const root = createRoot(container);
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
  const { createRoot } = await import("react-dom/client");
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  const root = createRoot(container);
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
  const { createRoot } = await import("react-dom/client");
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  const root = createRoot(container);
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

function completedProjection(runId: string, text: string): RunInputsProjection {
  const projection = questionProjection(runId, { state: "inactive", status: "resolved" });
  projection.inputs = [
    { input_id: `${runId}-text`, kind: "text", text, question_id: null, answers: null, status: "applied", created_at: "2026-10-07T00:00:01Z" },
    { input_id: `${runId}-answer`, kind: "answer", text: null, question_id: "question-batch-1", answers: { q0: ["o0", "o1"] }, status: "applied", created_at: "2026-10-07T00:00:02Z" },
  ];
  return projection;
}

test("restores and pages completed Run input history through the next Run and a remount", async () => {
  const env = installDom();
  const { createRoot } = await import("react-dom/client");
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  let root = createRoot(container);
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
  const render = async () => { await act(async () => { root.render(createElement(Probe)); await flush(); }); };
  try {
    await render();
    assert.match(container.textContent ?? "", /previous continuation/);
    assert.match(container.textContent ?? "", /current continuation/);
    assert.match(container.textContent ?? "", /Brief、Detailed/);
    assert.doesNotMatch(container.textContent ?? "", /q0|o0|o1/);
    assert.equal(container.querySelector('[data-run-input-history-run="run-previous"]')?.getAttribute("data-run-input-read-only"), "true");
    await act(async () => { assert.equal(await controls().loadMoreHistory(), true); });
    assert.equal(requested.at(-1), "run-previous");
    assert.match(container.textContent ?? "", /older continuation/);
    assert.equal(controls().historyHasMore, false);
    const historyRegion = container.querySelector<HTMLDivElement>("[data-run-input-history]");
    assert.ok(historyRegion);
    historyRegion.scrollTop = 200;
    runId = "run-next";
    await render();
    assert.match(container.textContent ?? "", /older continuation/);
    assert.match(container.textContent ?? "", /current continuation/);
    assert.equal(controls().history.filter((run) => run.run_id === "run-next").length, 1);
    assert.equal(container.querySelector('[data-run-input-history-run="run-current"] input'), null);
    assert.equal(container.querySelector('[data-run-input-history-run="run-next"] input')?.hasAttribute("disabled"), false);
    assert.equal(container.querySelector("[data-run-input-history-run]")?.getAttribute("data-run-input-history-run"), "run-next");
    assert.equal(historyRegion.scrollTop, 0, "a new pending question must be visible before older history");
    await act(async () => root.unmount());
    root = createRoot(container);
    await render();
    assert.match(container.textContent ?? "", /current continuation/);
    assert.match(container.textContent ?? "", /previous continuation/);
    await act(async () => { await controls().loadMoreHistory(); });
    assert.match(container.textContent ?? "", /older continuation/);
  } finally {
    await act(async () => root.unmount());
    sessionApi.getRunInputs = originalGet;
    env.restore();
  }
});

test("refresh traverses a disjoint history gap in order even when its first page overlaps a live projection", async () => {
  const env = installDom();
  const { createRoot } = await import("react-dom/client");
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  const root = createRoot(container);
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
  const { createRoot } = await import("react-dom/client");
  const container = env.dom.window.document.getElementById("root");
  assert.ok(container);
  const root = createRoot(container);
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
    assert.match(container.textContent ?? "", /safe continuation/);
    assert.doesNotMatch(container.textContent ?? "", /private continuation/);
    fail = true;
    await act(async () => { assert.equal(await controls().refreshHistory(), false); });
    assert.equal(controls().historyLoadFailed, true);
    assert.match(container.textContent ?? "", /safe continuation/);
    assert.ok(container.querySelector("[data-run-input-history-failure]"));
    await act(async () => { controls().retire(); });
    assert.doesNotMatch(container.textContent ?? "", /safe continuation/);
  } finally {
    await act(async () => root.unmount());
    sessionApi.getRunInputs = originalGet;
    env.restore();
  }
});
