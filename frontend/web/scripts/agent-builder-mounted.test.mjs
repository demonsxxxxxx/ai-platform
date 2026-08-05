#!/usr/bin/env node
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { captureScreenshot, startBrowser } from "./browser-smoke-harness.mjs";

const baseUrl = (process.argv[2] || "http://127.0.0.1:3001").replace(/\/+$/, "");
const evidenceDir = resolve("../../.codex-tmp/agent-builder-mounted-test");

function mountExpression() {
  return `
    (async () => {
      document.body.innerHTML = '<div id="agent-builder-test-root"></div>';
      const harnessModule = await import('/src/components/agent-builder/__tests__/agentBuilderMountedHarness.test.tsx');
      window.__agentBuilderMountedHarness = harnessModule.mountAgentBuilderHarness(
        document.getElementById('agent-builder-test-root'),
      );
      return true;
    })()
  `;
}

function clickByTextExpression(text) {
  return `(() => {
    const node = Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.includes(${JSON.stringify(text)}));
    if (!node) throw new Error('button_not_found:${text}');
    node.focus();
    node.click();
    return true;
  })()`;
}

function clickSelectorExpression(selector) {
  return `(() => {
    const node = document.querySelector(${JSON.stringify(selector)});
    if (!node) throw new Error('control_not_found:${selector}');
    node.focus();
    node.click();
    return true;
  })()`;
}

function setControlExpression(selector, value) {
  return `(() => {
    const control = document.querySelector(${JSON.stringify(selector)});
    if (!control) throw new Error('control_not_found:${selector}');
    const prototype = control instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLTextAreaElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(control, ${JSON.stringify(value)});
    control.dispatchEvent(new Event('input', { bubbles: true }));
    control.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  })()`;
}

const browser = await startBrowser({
  viewport: { width: 1440, height: 900, mobile: false },
  profilePrefix: "agent-builder-mounted-",
});

try {
  await browser.client.send("Page.navigate", { url: `${baseUrl}/auth/login` });
  await browser.client.waitFor("document.readyState === 'complete'", "vite_page_ready");
  await browser.client.evaluate(mountExpression());
  await browser.client.waitFor(
    "Boolean(document.querySelector('[data-agent-builder-workbench]'))",
    "agent_builder_mount",
  );

  await browser.client.evaluate(clickByTextExpression("Select Skill"));
  await browser.client.waitFor("Boolean(document.querySelector('[role=dialog]'))", "skills_dialog_open");
  const focusTrapReady = await browser.client.evaluate(
    "document.activeElement?.getAttribute('aria-label') === 'Close Skills'",
  );
  await browser.client.evaluate(
    "document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }))",
  );
  const focusTrapWrapped = await browser.client.evaluate(
    "document.activeElement?.textContent?.includes('document-review') === true",
  );
  await browser.client.evaluate(
    "document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))",
  );
  await browser.client.waitFor("!document.querySelector('[role=dialog]')", "skills_dialog_closed");
  const focusRestored = await browser.client.evaluate(
    "document.activeElement?.textContent?.trim() === 'Select Skill'",
  );

  await browser.client.evaluate(clickByTextExpression("Edit"));
  await browser.client.waitFor("Boolean(document.querySelector('[role=dialog]'))", "instructions_dialog_open");
  await browser.client.evaluate(clickSelectorExpression('[role=dialog] textarea'));
  await browser.client.evaluate(setControlExpression('[role=dialog] textarea', "First local instruction"));
  const typingFocusStable = await browser.client.evaluate(
    "document.activeElement === document.querySelector('[role=dialog] textarea')",
  );
  await browser.client.evaluate(setControlExpression('[role=dialog] textarea', "Second local instruction"));
  const repeatedTypingFocusStable = await browser.client.evaluate(
    "document.activeElement === document.querySelector('[role=dialog] textarea')",
  );
  await browser.client.evaluate(
    "document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))",
  );
  await browser.client.waitFor("!document.querySelector('[role=dialog]')", "instructions_dialog_closed");
  const instructionsFocusRestored = await browser.client.evaluate(
    "document.activeElement?.textContent?.trim() === 'Edit'",
  );

  await browser.client.evaluate(clickByTextExpression("Select Skill"));
  await browser.client.evaluate(clickByTextExpression("document-review"));
  await browser.client.evaluate(clickSelectorExpression('button[aria-label="Configure MCP tools"]'));
  await browser.client.waitFor("Boolean(document.querySelector('[role=dialog]'))", "tools_dialog_open");
  await browser.client.evaluate(clickSelectorExpression('[role=dialog] input[type=checkbox]'));
  const checkboxFocusStable = await browser.client.evaluate(
    "document.activeElement === document.querySelector('[role=dialog] input[type=checkbox]')",
  );
  await browser.client.evaluate(
    "document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))",
  );
  await browser.client.waitFor("!document.querySelector('[role=dialog]')", "tools_dialog_closed");
  const checkboxFocusRestored = await browser.client.evaluate(
    "document.activeElement?.getAttribute('aria-label') === 'Configure MCP tools'",
  );
  await browser.client.evaluate(setControlExpression("select", "platform-model"));
  await browser.client.evaluate(setControlExpression('textarea[aria-label="Preview message"]', "Review the authorized document"));
  await browser.client.evaluate(clickByTextExpression("Open Chat run"));
  await browser.client.waitFor(
    "window.__agentBuilderMountedHarness.handoffs.length === 1",
    "chat_handoff",
  );

  const verification = await browser.client.evaluate(`(() => ({
    focusTrapReady: ${Boolean(focusTrapReady)},
    focusTrapWrapped: ${Boolean(focusTrapWrapped)},
    focusRestored: ${Boolean(focusRestored)},
    typingFocusStable: ${Boolean(typingFocusStable)},
    repeatedTypingFocusStable: ${Boolean(repeatedTypingFocusStable)},
    instructionsFocusRestored: ${Boolean(instructionsFocusRestored)},
    checkboxFocusStable: ${Boolean(checkboxFocusStable)},
    checkboxFocusRestored: ${Boolean(checkboxFocusRestored)},
    calls: window.__agentBuilderMountedHarness.calls,
    handoffs: window.__agentBuilderMountedHarness.handoffs,
    dialogOpen: Boolean(document.querySelector('[role=dialog]')),
    overflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))()`);
  const screenshot = await captureScreenshot(browser.client, evidenceDir, "agent-builder-mounted");
  const expectedCall = verification.calls?.[0];
  const expectedHandoff = verification.handoffs?.[0];
  if (
    !verification.focusTrapReady ||
    !verification.focusTrapWrapped ||
    !verification.focusRestored ||
    !verification.typingFocusStable ||
    !verification.repeatedTypingFocusStable ||
    !verification.instructionsFocusRestored ||
    !verification.checkboxFocusStable ||
    !verification.checkboxFocusRestored ||
    verification.dialogOpen ||
    !verification.overflow ||
    expectedCall?.message !== "Review the authorized document" ||
    expectedCall?.options?.model !== "platform/model" ||
    expectedCall?.selectedSkill?.skill_id !== "document-review" ||
    expectedCall?.selectedSkill?.expected_version !== "2026.07.27" ||
    JSON.stringify(expectedCall?.selectedMcpToolIds) !==
      JSON.stringify(["mcp:knowledge:search"]) ||
    "instructions" in (expectedCall ?? {}) ||
    expectedHandoff?.path !== "/chat/session-mounted" ||
    expectedHandoff?.runId !== "run-mounted"
  ) {
    throw new Error(`mounted_agent_builder_verification_failed:${JSON.stringify(verification)}`);
  }
  mkdirSync(evidenceDir, { recursive: true });
  writeFileSync(
    resolve(evidenceDir, "evidence.json"),
    `${JSON.stringify({ baseUrl, status: "passed", screenshot, verification }, null, 2)}\n`,
  );
  console.log(JSON.stringify({ baseUrl, status: "passed", screenshot, verification }));
} finally {
  await browser.close();
}
