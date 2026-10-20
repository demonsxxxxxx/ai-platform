import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { createElement } from "react";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import { createLobeServerTestRoot } from "../../../components/common/__tests__/lobeUiTestRoot";
const renderToStaticMarkup = await createLobeServerTestRoot();
// @ts-expect-error jsdom 26 does not ship declarations.
import { JSDOM } from "jsdom";
import { AssistantUiTestProvider } from "../../../components/chat/assistant-ui/__tests__/assistantUiTestProvider";
import { ChatInputToolbar } from "../../../components/chat/ChatInputToolbar.tsx";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const translations = createInstance();
await translations.init({
  lng: "en", initImmediate: false,
  resources: { en: { translation: { chat: { stop: "Stop" } } } },
});

test("the stop confirmation awaits the command result before showing acknowledgement", () => {
  const source = readFileSync(
    resolve(__dirname, "../../../components/chat/ChatInput.tsx"),
    "utf8",
  );
  const confirmStart = source.indexOf('<ConfirmDialog\n        isOpen={stopConfirmOpen}');
  const contactDialogStart = source.indexOf("<ContactAdminDialog", confirmStart);
  assert.notEqual(confirmStart, -1);
  assert.notEqual(contactDialogStart, -1);

  const confirm = source.slice(confirmStart, contactDialogStart);
  const awaitStop = confirm.indexOf("result = await onStop()");
  const closeDialog = confirm.indexOf("setStopConfirmOpen(false)", awaitStop);
  const acknowledged = confirm.indexOf('result !== "acknowledged"');
  const requestedCopy = confirm.indexOf('t("chat.runStatus.event.cancelRequested")');

  assert.ok(awaitStop >= 0 && acknowledged > awaitStop);
  assert.ok(closeDialog > acknowledged && requestedCopy > closeDialog);
  assert.match(confirm, /loading=\{isStopSubmitting\}/);
  assert.doesNotMatch(confirm, /chat\.status\.cancelled/);
});

for (const [isLoading, canStop, canSend, expectedStop] of [
  [true, true, true, true],
  [true, false, true, false],
  [false, true, true, false],
  [true, true, false, false],
]) {
  test(`composer stop control: loading=${isLoading}, owner=${canStop}, permission=${canSend}`, () => {
    const noop = () => {};
    const toolbar = createElement(ChatInputToolbar, {
      activePanel: null, onActivePanelChange: noop, canSend, isLoading, canStop,
      canSubmit: false, hasUploadingAttachment: false, enabledToolsCount: 0,
      totalToolsCount: 0, enabledSkillsCount: 0, totalSkillsCount: 0,
      availableModels: [], uploadCategories: [], uploadFiles: noop,
      onNoPermissionClick: noop,
    });
    const markup = renderToStaticMarkup(createElement(I18nextProvider, { i18n: translations },
      createElement(AssistantUiTestProvider, { isRunning: isLoading }, toolbar)));
    const dom = new JSDOM(markup);
    try {
      const stop = dom.window.document.querySelector('button[title="Stop"]');
      assert.equal(stop !== null, expectedStop);
      if (isLoading && canSend && !canStop) {
        assert.ok(dom.window.document.querySelector('button[aria-disabled="true"][aria-busy="true"], button[disabled][aria-busy="true"]'));
        assert.equal(dom.window.document.querySelector('button[type="submit"]'), null);
      }
    } finally {
      dom.window.close();
    }
  });
}
