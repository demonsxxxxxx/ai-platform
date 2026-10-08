import assert from "node:assert/strict";
import test from "node:test";
// jsdom 26 is the pinned mounted-test runtime and does not ship declarations.
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import { act, createElement } from "react";

import { RunQuestionCard } from "../../../components/chat/RunQuestionCard.tsx";
import type {
  RunInputRecord,
  RunInputSubmissionRequest,
  RunInputsProjection,
} from "../../../services/api/session.ts";
import { sessionApi } from "../../../services/api/session.ts";
import { installBrowserAuthTestDb } from "../../__tests__/browserAuthTestDb.ts";
import { useRunInputs } from "../runInputs.ts";
import type { RunInputsController } from "../types.ts";

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
            question: "Which formats should I include?",
            header: "Output",
            multiSelect: true,
            options: [
              { label: "Brief", description: "A short summary" },
              { label: "Detailed", description: "More explanation" },
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
      "Which formats should I include?": ["Brief", "Detailed"],
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

test("keeps text draft on an uncertain result and retries the same input ID to the same Run", async () => {
  const env = installDom();
  const { createRoot } = await import("react-dom/client");
  installBrowserAuthTestDb();
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
  const [{ ChatInput }, { AuthProvider }, { authApi }, { MemoryRouter }] = await Promise.all([
    import("../../../components/chat/ChatInput.tsx"),
    import("../../useAuth.tsx"),
    import("../../../services/api/auth.ts"),
    import("react-router-dom"),
  ]);
  const originalGetCurrentUser = authApi.getCurrentUser;
  const originalBootstrapAuthContext = authApi.bootstrapAuthContext;
  authApi.getCurrentUser = async () => {
    throw Object.assign(new Error("unauthenticated"), { status: 401 });
  };
  authApi.bootstrapAuthContext = async (request) => ({
    status: "ready",
    protocol_version: 2,
    generation: request.generation,
  });

  let sendCalls = 0;
  let canSend = false;
  const emptySelections: never[] = [];
  const optionValues = {};
  function Probe() {
    const runInputs = useRunInputs({
      sessionId: "session-text",
      runId: "run-text",
      isRunActive: true,
    });
    return createElement(
      MemoryRouter,
      null,
      createElement(
        AuthProvider,
        null,
        createElement(ChatInput, {
          onSend: async () => {
            sendCalls += 1;
            return { status: "accepted" as const };
          },
          onStop: async () => "unavailable" as const,
          isLoading: true,
          acceptedFileTypes: emptySelections,
          tools: emptySelections,
          skills: emptySelections,
          availableModels: emptySelections,
          agentOptionValues: optionValues,
          disableSlashCommands: true,
          canSend,
          runInputs,
        }),
      ),
    );
  }
  const typeText = async (value: string) => {
    await act(async () => {
      const textarea = container.querySelector<HTMLTextAreaElement>("[data-run-input-form] textarea");
      assert.ok(textarea);
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      assert.ok(setter);
      setter.call(textarea, value);
      textarea.dispatchEvent(new env.dom.window.InputEvent("input", {
        bubbles: true,
        data: value,
        inputType: "insertText",
      }));
      textarea.dispatchEvent(new env.dom.window.Event("change", { bubbles: true }));
    });
  };
  const restore = () => {
    sessionApi.getRunInputs = originalGet;
    sessionApi.submitRunInput = originalSubmit;
    authApi.getCurrentUser = originalGetCurrentUser;
    authApi.bootstrapAuthContext = originalBootstrapAuthContext;
  };

  try {
    await act(async () => {
      root.render(createElement(Probe));
      await flush();
    });
    await typeText("please add one example");
    assert.ok(container.querySelector<HTMLTextAreaElement>("[data-run-input-form] textarea")?.readOnly);
    await act(async () => {
      container.querySelector("[data-run-input-form]")?.dispatchEvent(
        new env.dom.window.Event("submit", { bubbles: true, cancelable: true }),
      );
      await flush();
    });
    assert.equal(submissions.length, 0);
    canSend = true;
    await act(async () => root.render(createElement(Probe)));
    await act(async () => {
      container.querySelector("[data-run-input-form] textarea")?.dispatchEvent(
        new env.dom.window.KeyboardEvent("keydown", {
          key: "Enter", isComposing: true, bubbles: true, cancelable: true,
        }),
      );
      await flush();
    });
    assert.equal(submissions.length, 0);
    await act(async () => {
      const form = container.querySelector("[data-run-input-form]");
      assert.ok(form);
      form.dispatchEvent(new env.dom.window.Event("submit", { bubbles: true, cancelable: true }));
      await flush();
    });
    assert.equal(submissions.length, 1);
    assert.equal(container.querySelector<HTMLTextAreaElement>("[data-run-input-form] textarea")?.value,
      "please add one example");
    assert.ok(container.querySelector("[data-run-input-composer] [role='status'] button"));

    await act(async () => {
      const retry = container.querySelector<HTMLButtonElement>("[data-run-input-composer] [role='status'] button");
      assert.ok(retry);
      retry.click();
      await flush();
    });
    assert.equal(submissions.length, 2);
    assert.equal(submissions[0].runId, "run-text");
    assert.equal(submissions[1].runId, "run-text");
    assert.deepEqual(submissions[1].body, submissions[0].body);
    assert.ok("text" in submissions[1].body);
    assert.equal(submissions[1].body.text, "please add one example");
    assert.equal(container.querySelector<HTMLTextAreaElement>("[data-run-input-form] textarea")?.value, "");
    assert.equal(sendCalls, 0);
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
