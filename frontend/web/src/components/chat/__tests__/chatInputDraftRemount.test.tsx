import assert from "node:assert/strict";
import test from "node:test";
// jsdom 26 ships no declarations; this test uses only its runtime constructor.
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";
import { act } from "react";
import type { SubmissionOutcome } from "../../../hooks/useAgent/types";
import type {
  ChatInputDraftSnapshot,
  ChatInputProps,
} from "../chatInputTypes";

function installDom() {
  const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
    url: "http://localhost/",
  });
  const window = dom.window;
  Object.defineProperties(globalThis, {
    window: { configurable: true, value: window },
    document: { configurable: true, value: window.document },
    navigator: { configurable: true, value: window.navigator },
    HTMLElement: { configurable: true, value: window.HTMLElement },
    Element: { configurable: true, value: window.Element },
    Node: { configurable: true, value: window.Node },
    HTMLTextAreaElement: {
      configurable: true,
      value: window.HTMLTextAreaElement,
    },
    Event: { configurable: true, value: window.Event },
    InputEvent: { configurable: true, value: window.InputEvent },
    CustomEvent: { configurable: true, value: window.CustomEvent },
    localStorage: { configurable: true, value: window.localStorage },
    sessionStorage: { configurable: true, value: window.sessionStorage },
    requestAnimationFrame: {
      configurable: true,
      value: (callback: FrameRequestCallback) =>
        window.setTimeout(() => callback(Date.now()), 0),
    },
    cancelAnimationFrame: {
      configurable: true,
      value: (handle: number) => window.clearTimeout(handle),
    },
    IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: true },
  });
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
  return dom;
}

function deferredSubmission() {
  let resolve!: (outcome: SubmissionOutcome) => void;
  const promise = new Promise<SubmissionOutcome>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

test("keeps drafts local across remounts, submissions, and session scopes", async () => {
  const dom = installDom();
  const { createRoot } = await import("react-dom/client");
  const { ChatInput } = await import("../ChatInput");
  const { AuthProvider } = await import("../../../hooks/useAuth");
  const rootNode = document.getElementById("root");
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  const snapshotRef: { current: ChatInputDraftSnapshot } = {
    current: {
      value: "",
      appliedInitialDraftKey: null,
      scopeKey: null,
      revision: 0,
      selectedSkillState: undefined,
      selectedSkillRevision: 0,
      pendingScopeHandoff: false,
    },
  };
  let submission = deferredSubmission();
  let attachments: NonNullable<ChatInputProps["attachments"]> = [];
  let selectedSkillState: ChatInputProps["selectedSkillState"];
  let clearSelectedSkillCalls = 0;
  const onSend = () => submission.promise;
  const acceptedFileTypes: string[] = [];
  const tools: NonNullable<ChatInputProps["tools"]> = [];
  const skills: NonNullable<ChatInputProps["skills"]> = [];
  const agentOptionValues: NonNullable<ChatInputProps["agentOptionValues"]> = {};
  const availableModels: NonNullable<ChatInputProps["availableModels"]> = [];

  function Harness({
    optimistic,
    scopeKey,
    handoffKey,
  }: {
    optimistic: boolean;
    scopeKey: string | null;
    handoffKey: string | null;
  }) {
    const composer = (
      <ChatInput
        draftSnapshotRef={snapshotRef}
        draftScopeKey={scopeKey}
        draftScopeHandoffKey={handoffKey}
        onSend={onSend}
        onStop={async () => "unavailable"}
        isLoading={false}
        attachments={attachments}
        onAttachmentsChange={(next) => {
          attachments =
            typeof next === "function" ? next(attachments) : next;
        }}
        selectedSkillState={selectedSkillState}
        onClearSelectedSkill={() => {
          clearSelectedSkillCalls += 1;
        }}
        acceptedFileTypes={acceptedFileTypes}
        disableSlashCommands
        tools={tools}
        skills={skills}
        enableSkills={false}
        agentOptionValues={agentOptionValues}
        availableModels={availableModels}
      />
    );
    return optimistic ? (
      <section data-layout="messages"><div>{composer}</div></section>
    ) : (
      <main data-layout="empty">{composer}</main>
    );
  }

  const render = async (
    optimistic: boolean,
    scopeKey: string | null,
    handoffKey: string | null = null,
  ) => {
    await act(async () => {
      root.render(
        <AuthProvider>
          <Harness
            optimistic={optimistic}
            scopeKey={scopeKey}
            handoffKey={handoffKey}
          />
        </AuthProvider>,
      );
    });
  };
  const textarea = () => {
    const element = rootNode.querySelector("textarea");
    assert.ok(element instanceof HTMLTextAreaElement);
    return element;
  };
  const typeDraft = async (value: string) => {
    await act(async () => {
      const element = textarea();
      const setter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value",
      )?.set;
      assert.ok(setter);
      setter.call(element, value);
      element.dispatchEvent(
        new dom.window.InputEvent("input", {
          bubbles: true,
          data: value,
          inputType: "insertText",
        }),
      );
      element.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    });
  };
  const submit = async () => {
    await act(async () => {
      const form = rootNode.querySelector("form");
      assert.ok(form);
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
  };

  try {
    await render(false, null);
    await typeDraft("external navigation task");
    await submit();
    await typeDraft("must not cross sessions");
    await render(true, "session-external");
    assert.equal(textarea().value, "");
    await act(async () => submission.resolve({ status: "failed" }));

    submission = deferredSubmission();
    await render(false, null);
    await typeDraft("failed first task");
    await submit();
    await act(async () => submission.resolve({ status: "failed" }));
    assert.equal(textarea().value, "failed first task");
    await render(true, "session-failed", "session-failed");
    assert.equal(textarea().value, "failed first task");
    await render(false, "session-after-failure", "session-failed");
    assert.equal(textarea().value, "");

    submission = deferredSubmission();
    await render(false, null);
    await typeDraft("first task");
    assert.equal(snapshotRef.current.value, "first task");
    await submit();
    await typeDraft("next task");
    await act(async () => submission.resolve({ status: "accepted" }));
    assert.equal(textarea().value, "next task");

    await render(true, "session-1", "session-1");
    assert.equal(textarea().value, "next task");

    await render(false, "session-2", "session-1");
    assert.equal(textarea().value, "");

    await typeDraft("keep after failure");
    submission = deferredSubmission();
    await submit();
    await render(true, "session-2");
    assert.equal(textarea().value, "keep after failure");
    await act(async () => submission.resolve({ status: "failed" }));
    assert.equal(textarea().value, "keep after failure");

    submission = deferredSubmission();
    await submit();
    await render(false, "session-2");
    assert.equal(textarea().value, "keep after failure");
    await act(async () => submission.resolve({ status: "accepted" }));
    assert.equal(textarea().value, "");
    clearSelectedSkillCalls = 0;

    const submittedAttachment = {
      id: "attachment-a",
      key: "attachment-a",
      name: "a.txt",
      type: "document" as const,
      mimeType: "text/plain",
      size: 1,
    };
    const newerAttachment = {
      id: "attachment-b",
      key: "attachment-b",
      name: "b.txt",
      type: "document" as const,
      mimeType: "text/plain",
      size: 1,
    };
    attachments = [submittedAttachment];
    submission = deferredSubmission();
    await render(false, "session-2");
    await typeDraft("same task");
    await submit();
    await typeDraft("temporary edit");
    await typeDraft("same task");
    attachments = [submittedAttachment, newerAttachment];
    selectedSkillState = {
      selectedSkill: {
        name: "newer-skill",
        expected_version: "v2",
      },
      status: "confirmed",
      recoveryCode: null,
      requiresReconfirmation: false,
    } as NonNullable<ChatInputProps["selectedSkillState"]>;
    await render(true, "session-2");
    await act(async () => submission.resolve({ status: "accepted" }));
    assert.equal(textarea().value, "same task");
    assert.deepEqual(
      attachments.map((attachment) => attachment.id),
      ["attachment-b"],
    );
    assert.equal(clearSelectedSkillCalls, 0);
  } finally {
    await act(async () => root.unmount());
    dom.window.close();
  }
});
