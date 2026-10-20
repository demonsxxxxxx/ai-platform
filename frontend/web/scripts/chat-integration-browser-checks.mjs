import assert from "node:assert/strict";
import { captureScreenshot } from "./browser-smoke-harness.mjs";

async function key(client, key, code, virtualKey, modifiers = 0) {
  for (const type of ["keyDown", "keyUp"]) {
    await client.send("Input.dispatchKeyEvent", { type, key, code, windowsVirtualKeyCode: virtualKey, modifiers });
  }
}

async function sendFixtureTurn(client, text, turn) {
  await client.evaluate(`(() => {
    const input = document.querySelector('textarea');
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, ${JSON.stringify(text)});
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await client.waitFor(`!document.querySelector('button[aria-label="发送消息"]').disabled`, "chat:send-ready");
  await client.evaluate(`document.querySelector('button[aria-label="发送消息"]').click()`);
  await client.waitFor(`window.__chatIntegration.sends === ${turn} && Boolean(document.querySelector('[data-message-id="assistant-synthetic-${turn}"]'))`, "chat:next-turn");
}

export async function checkChatIntegration(client, viewport, scenario, { theme, evidenceDir }) {
  const prefix = `${theme}-${viewport.name}-${scenario}`;
  await client.evaluate(`document.querySelector('[data-agent-starter-prompts] button').click()`);
  await client.waitFor(`document.querySelector('textarea').value.length > 0`, "chat:suggestion-draft");
  const draft = await client.evaluate(`document.querySelector('textarea').value`);
  await client.evaluate(`(() => { const toggle = document.querySelector('[data-librechat-context-toggle]'); toggle.focus(); toggle.click(); })()`);
  await client.waitFor(`document.querySelectorAll('[data-workbench-region=context] [role=tabpanel]:not([hidden]) [role=treeitem]').length === 48`, "chat:files-loaded");
  await client.waitFor(`(() => {
    const popup = document.querySelector('[data-drawer-placement]');
    return !popup || popup.getAnimations({ subtree: true }).every(a => a.effect?.getTiming().iterations === Infinity || a.playState !== 'running');
  })()`, "chat:drawer-painted");
  await client.evaluate(`document.querySelector('#profile-drive-public-tab').click()`);
  await client.waitFor(`document.querySelector('#profile-drive-public-tab').getAttribute('aria-selected') === 'true' && !document.querySelector('#profile-drive-public-panel').hidden`, "chat:public-files");
  await client.evaluate(`document.querySelector('#profile-drive-profile-tab').click()`);
  await client.waitFor(`!document.querySelector('#profile-drive-profile-panel').hidden`, "chat:personal-files");
  const geometry = await client.evaluate(`(() => {
    const thread = document.querySelector('[data-workbench-region=thread]');
    const column = thread.parentElement;
    const context = document.querySelector('[data-workbench-region=context]');
    const popup = context.closest('[role=dialog]');
    const rect = n => { const r = n.getBoundingClientRect(); return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, width: r.width, height: r.height }; };
    const scroll = context.querySelector('[data-librechat-context-overview]');
    const before = window.scrollY;
    scroll.scrollTop = scroll.scrollHeight;
    return { thread: rect(column), context: rect(context), popup: popup ? rect(popup) : null,
      gridChildren: column.parentElement.children.length, scrollable: scroll.scrollHeight > scroll.clientHeight,
      scrolled: scroll.scrollTop > 0, scrollWidth: scroll.getBoundingClientRect().width, pageUnmoved: window.scrollY === before,
      focusedInside: Boolean(popup?.contains(document.activeElement)), bodyWidth: document.body.scrollWidth };
  })()`);
  assert.ok(geometry.scrollable && geometry.scrolled && geometry.pageUnmoved, JSON.stringify(geometry));
  assert.ok(geometry.scrollWidth >= geometry.context.width - 40, "file content fills the pane width");
  assert.ok(geometry.bodyWidth <= viewport.width + 1, JSON.stringify(geometry));
  if (viewport.width >= 1280) {
    assert.equal(geometry.gridChildren, 2, "desktop grid contains only thread and files");
    assert.equal(geometry.popup, null);
    assert.ok(Math.abs(geometry.thread.y - geometry.context.y) < 2);
    assert.ok(geometry.context.x >= geometry.thread.right - 1);
    assert.ok(geometry.context.width >= 288 && geometry.context.width <= 321);
  } else {
    assert.ok(geometry.popup && geometry.popup.x >= 0 && geometry.popup.right <= viewport.width + 1);
    assert.ok(geometry.popup.bottom <= viewport.height + 1 && geometry.focusedInside);
    await client.evaluate(`(() => { const buttons = [...document.querySelectorAll('[role=dialog] button')].filter(n => !n.disabled && n.getBoundingClientRect().width > 0); buttons.at(-1).focus(); })()`);
    await key(client, "Tab", "Tab", 9);
    await client.waitFor(`document.querySelector('[role=dialog]').contains(document.activeElement)`, "chat:drawer-tab-trap");
    await key(client, "Tab", "Tab", 9, 8);
    await client.waitFor(`document.querySelector('[role=dialog]').contains(document.activeElement)`, "chat:drawer-shift-tab-trap");
  }
  await client.evaluate(`(() => { document.querySelector('#profile-drive-profile-tab').focus(); document.querySelector('[data-librechat-context-overview]').scrollTop = 0; })()`);
  const filesScreenshot = await captureScreenshot(client, evidenceDir, `${prefix}-files`);
  await key(client, "Escape", "Escape", 27);
  await client.waitFor(`!document.querySelector('[data-workbench-region=context]') && document.activeElement === document.querySelector('[data-librechat-context-toggle]')`, "chat:files-close-focus");
  assert.equal(await client.evaluate(`document.querySelector('textarea').value`), draft);
  if (viewport.width < 1280) {
    await client.evaluate(`document.querySelector('[data-librechat-context-toggle]').click()`);
    await client.waitFor(`Boolean(document.querySelector('[data-librechat-context-backdrop]'))`, "chat:backdrop-open");
    for (const type of ["mousePressed", "mouseReleased"]) {
      await client.send("Input.dispatchMouseEvent", { type, x: 4, y: viewport.height / 2, button: "left", clickCount: 1 });
    }
    await client.waitFor(`!document.querySelector('[data-workbench-region=context]')`, "chat:backdrop-close");
  }
  if (scenario !== "chat-integration") return { geometry, filesScreenshot, suggestionDraft: true };

  await client.evaluate(`(() => { window.__chatCopies = []; Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async text => window.__chatCopies.push(text) } }); document.querySelector('button[aria-label="发送消息"]').click(); })()`);
  await client.waitFor(`window.__chatIntegration.sends === 1 && Boolean(document.querySelector('button[aria-label="停止生成"]'))`, "chat:send-once");
  await client.waitFor(`document.querySelector('[data-message-id="user-synthetic-1"]')?.textContent.includes('已确认附件.pdf')`, "chat:hydrated-attachment");
  await client.waitFor(`Boolean(document.querySelector('[data-assistant-ui-stream-status="waiting"]'))`, "chat:first-content-waiting");
  await client.waitFor(`(() => {
    const status = document.querySelector('[data-assistant-ui-stream-status="waiting"]');
    if (!status) return false;
    for (let node = status; node; node = node.parentElement) if (Number(getComputedStyle(node).opacity) < 0.99) return false;
    const box = status.getBoundingClientRect();
    return box.width > 0 && box.top >= 0 && box.bottom <= document.querySelector('[data-virtuoso-scroller]').getBoundingClientRect().bottom;
  })()`, "chat:waiting-painted");
  const waitingScreenshot = await captureScreenshot(client, evidenceDir, `${prefix}-waiting`);
  await client.waitFor(`document.querySelectorAll('[data-chat-activity-status]').length >= 4`, "chat:tool-statuses");
  const entryClearOfMessage = await client.evaluate(`(() => {
    const toggle = document.querySelector('[data-librechat-context-toggle]').getBoundingClientRect();
    const message = document.querySelector('[data-message-id="user-synthetic-1"]').getBoundingClientRect();
    return toggle.bottom <= message.top || toggle.left >= message.right || toggle.right <= message.left;
  })()`);
  assert.ok(entryClearOfMessage, JSON.stringify({ entryClearOfMessage, toggle: await client.evaluate(`(() => { const node = document.querySelector('[data-librechat-context-toggle]'); const box = node?.getBoundingClientRect(); return box && { x: box.x, y: box.y, right: box.right, bottom: box.bottom, width: box.width, height: box.height }; })()`), message: await client.evaluate(`(() => { const node = document.querySelector('[data-message-id="user-synthetic-1"]'); const box = node?.getBoundingClientRect(); return box && { x: box.x, y: box.y, right: box.right, bottom: box.bottom, width: box.width, height: box.height }; })()`) }));
  const activity = await client.evaluate(`([...document.querySelectorAll('[data-chat-activity-status]')]).map(node => {
    const row = node.firstElementChild; const text = node.querySelector('span'); const style = getComputedStyle(row);
    return { status: node.dataset.chatActivityStatus, color: style.color, background: style.backgroundColor, fontSize: parseFloat(getComputedStyle(text || row).fontSize), text: text?.textContent || '', clickable: Boolean(node.querySelector('button')) };
  })`);
  for (const status of ["success", "error", "cancelled", "loading"]) {
    const row = activity.find(item => item.status === status);
    assert.ok(row && row.background !== "rgba(0, 0, 0, 0)" && row.fontSize >= 13 && !row.clickable, JSON.stringify(activity));
  }
  assert.notEqual(activity.find(item => item.status === "success").color, activity.find(item => item.status === "error").color);
  assert.equal(await client.evaluate(`document.querySelector('textarea').value`), "");
  await client.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "no-preference" }] });
  const reveal = await client.evaluate(`(async () => {
    const text = '平滑显示公开正文。'.repeat(300);
    window.__chatIntegration.advance(text);
    const lengths = [];
    for (let frame = 0; frame < 12; frame++) {
      await new Promise(requestAnimationFrame);
      lengths.push(document.querySelector('[data-message-id="assistant-synthetic-1"] .markdown-preview')?.textContent.length || 0);
    }
    return { sourceLength: text.length, lengths };
  })()`);
  assert.ok(reveal.lengths.some(length => length > 0 && length < reveal.sourceLength), JSON.stringify(reveal));
  await client.waitFor(`document.querySelector('[data-message-id="assistant-synthetic-1"] .markdown-preview')?.textContent.length === ${reveal.sourceLength}`, "chat:smooth-reveal-complete");
  await client.waitFor(`(() => { const view = document.querySelector('[data-virtuoso-scroller]'); return view.scrollHeight - view.scrollTop - view.clientHeight <= 100; })()`, "chat:animated-text-follows-bottom");
  await client.evaluate(`window.__chatIntegration.advance()`);
  await client.waitFor(`document.body.textContent.includes('正在整理文件。')`, "chat:stream-update");
  await client.evaluate(`window.__chatIntegration.showQuestions()`);
  await client.waitFor(`Boolean(document.querySelector('[data-run-question-card]'))`, "chat:question-card");
  for (const label of ["普通", "PDF", "DOCX"]) {
    await client.evaluate(`document.querySelector('[data-run-question-card] [aria-label="${label}"]').click()`);
  }
  await client.evaluate(`(() => { const note = document.querySelector('[data-run-question-card] fieldset:last-child textarea'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(note, '请保留目录结构'); note.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await client.evaluate(`document.querySelector('[data-run-question-card] button[type="submit"]').click()`);
  await client.waitFor(`Boolean(document.querySelector('[data-run-question-status="answered"]'))`, "chat:question-answered");
  const answerReceipt = await client.evaluate(`document.querySelector('[data-run-question-status="answered"]').textContent`);
  assert.match(answerReceipt, /答案已提交/);
  assert.doesNotMatch(answerReceipt, /问题已处理/);
  assert.deepEqual(await client.evaluate(`window.__chatIntegration.lastAnswers`), { priority: "normal", formats: ["pdf", "docx"], note: { text: "请保留目录结构" } });
  await client.evaluate(`window.__chatIntegration.resolveQuestions()`);
  await client.waitFor(`!document.querySelector('[data-run-question-status]')`, "chat:question-resolved");
  await client.evaluate(`document.querySelector('button[aria-label="停止生成"]').click()`);
  await client.waitFor(`Boolean(document.querySelector('[role=dialog]'))`, "chat:stop-confirm");
  assert.equal(await client.evaluate(`window.__chatIntegration.stops`), 0);
  await client.evaluate(`([...document.querySelectorAll('[role=dialog] button')].find(node => node.textContent.trim() === '取消')).click()`);
  await client.waitFor(`!document.querySelector('[role=dialog]')`, "chat:stop-dismissed");
  assert.equal(await client.evaluate(`window.__chatIntegration.stops`), 0);
  await client.evaluate(`document.querySelector('button[aria-label="停止生成"]').click()`);
  await client.waitFor(`Boolean(document.querySelector('[role=dialog]'))`, "chat:stop-reopen");
  await client.evaluate(`([...document.querySelectorAll('[role=dialog] button')].find(node => node.textContent.trim() === '停止生成')).click()`);
  await client.waitFor(`window.__chatIntegration.stops === 1 && document.body.textContent.includes('生成已取消') && !document.querySelector('[role=dialog]')`, "chat:stop-acknowledged");
  assert.ok(await client.evaluate(`document.querySelector('[data-message-id="assistant-synthetic-1"]').textContent.includes('正在整理文件。')`));
  const stoppedScreenshot = await captureScreenshot(client, evidenceDir, `${prefix}-stopped`);


  await sendFixtureTurn(client, "验证完成与富文本", 2);
  await client.evaluate(`window.__chatIntegration.advance(window.__chatIntegration.finalText)`);
  await client.waitFor(`Boolean(document.querySelector('[data-message-id="assistant-synthetic-2"] strong'))`, "chat:streaming-rich-text");
  await client.evaluate(`window.__chatIntegration.synchronize()`);
  await client.waitFor(`Boolean(document.querySelector('[data-assistant-ui-stream-status="synchronizing"]'))`, "chat:terminal-sync");
  assert.equal(await client.evaluate(`Boolean(document.querySelector('[data-message-id="assistant-synthetic-2"] [data-assistant-ui-message-actions]'))`), false);
  assert.equal(await client.evaluate(`Boolean(document.querySelector('button[aria-label="停止生成"]'))`), false);
  await client.evaluate(`window.__chatIntegration.finish()`);
  await client.waitFor(`!document.querySelector('[data-assistant-ui-stream-status]') && Boolean(document.querySelector('[data-message-id="assistant-synthetic-2"] [aria-label="复制"]'))`, "chat:terminal-complete");
  await client.waitFor(`Boolean(document.querySelector('[data-message-id="assistant-synthetic-2"] table')) && document.querySelector('[data-message-id="assistant-synthetic-2"] .cm-editor')?.textContent.includes('归档完成')`, "chat:rich-renderers-ready");
  await client.waitFor(`Boolean(document.querySelector('[data-message-id="assistant-synthetic-2"] .mermaid-diagram svg'))`, "chat:mermaid-rendered");
  await client.waitFor(`Boolean(document.querySelector('[data-message-id="assistant-synthetic-2"] [data-subagent-id="public-operation-root"] [data-subagent-id="public-operation-child"] [data-subagent-id="public-operation-grandchild"]'))`, "chat:subagent-depth");
  await client.waitFor(`Boolean(document.querySelector('[data-message-id="assistant-synthetic-2"] [data-slot="todo-list"]')) && Boolean(document.querySelector('[data-message-id="assistant-synthetic-2"] [data-slot="artifact-card"]'))`, "chat:todo-artifact");
  const publicSurface = await client.evaluate(`(() => { const message = document.querySelector('[data-message-id="assistant-synthetic-2"]'); const link = [...message.querySelectorAll('a')].find(node => node.textContent.includes('安全参考')); const artifact = message.querySelector('[data-slot="artifact-card"]'); const task = message.querySelector('[data-subagent-id="public-operation-root"]'); return { math: Boolean(message.querySelector('.katex')), safeLink: link?.getAttribute('href'), safeLinkTarget: link?.getAttribute('target'), longArtifactLabel: artifact?.getAttribute('aria-label'), subagent: task?.querySelector('button')?.getAttribute('aria-label'), longToolText: [...message.querySelectorAll('[data-chat-activity-status] span')].map(node => node.textContent).join('|') }; })()`);
  assert.equal(publicSurface.safeLink, 'https://example.com/reference');
  assert.equal(publicSurface.safeLinkTarget, '_blank');
  assert.ok(publicSurface.math && publicSurface.longArtifactLabel?.length > 40 && publicSurface.subagent?.includes('子代理') && publicSurface.longToolText.includes('公开工具名称'), JSON.stringify(publicSurface));
  await client.evaluate(`document.querySelector('[data-message-id="assistant-synthetic-2"] [data-assistant-ui-message-actions] button[aria-label="复制"]').click()`);
  await client.waitFor(`window.__chatCopies.at(-1) === window.__chatIntegration.finalText && Boolean(document.querySelector('[data-copied=true]'))`, "chat:native-copy");
  await client.evaluate(`document.querySelector('[data-message-id="assistant-synthetic-2"] [data-message-work-details-toggle]').click()`);
  await client.waitFor(`([...document.querySelectorAll('[data-message-id="assistant-synthetic-2"] [data-chat-activity-status]')]).every(node => node.getBoundingClientRect().height > 0)`, "chat:activity-expanded");
  await client.evaluate(`(() => { const view = document.querySelector('[data-virtuoso-scroller]'); view.scrollTop = view.scrollHeight; })()`);
  await client.waitFor(`(() => { const view = document.querySelector('[data-virtuoso-scroller]'); const code = document.querySelector('[data-message-id="assistant-synthetic-2"] .cm-editor').getBoundingClientRect(); return code.bottom <= view.getBoundingClientRect().bottom && code.height > 0; })()`, "chat:rich-text-reachable");
  const messagesScreenshot = await captureScreenshot(client, evidenceDir, `${prefix}-messages`);

  await sendFixtureTurn(client, "验证失败提示", 3);
  await client.evaluate(`window.__chatIntegration.advance('失败前已经收到的公开内容。')`);
  await client.waitFor(`document.body.textContent.includes('失败前已经收到的公开内容。')`, "chat:partial-before-failure");
  await client.evaluate(`window.__chatIntegration.fail()`);
  await client.waitFor(`document.querySelector('[data-message-id="assistant-synthetic-3"] [role=alert]')?.textContent.includes('执行失败')`, "chat:safe-failure");
  assert.equal(await client.evaluate(`Boolean(document.querySelector('[data-assistant-ui-stream-status]'))`), false);
  assert.ok(await client.evaluate(`document.querySelector('[data-message-id="assistant-synthetic-3"]').textContent.includes('失败前已经收到的公开内容。')`));
  assert.equal(await client.evaluate(`/private executor name|private-stage|private-error-secret/.test(document.body.textContent)`), false);
  const failureScreenshot = await captureScreenshot(client, evidenceDir, `${prefix}-failure`);
  await client.evaluate(`window.__chatIntegration.showGeneric()`);
  await client.waitFor(`Boolean(document.querySelector('[data-composer-model-trigger]'))`, "chat:model-trigger");
  await client.evaluate(`(() => { const trigger = document.querySelector('[data-composer-model-trigger]'); trigger.focus(); trigger.click(); })()`);
  await client.waitFor(`Boolean(document.querySelector('[data-composer-model-panel] input'))`, "chat:model-picker");
  await client.waitFor(`(() => { const panel = document.querySelector('[data-composer-model-panel]'); const box = panel.getBoundingClientRect(); return box.left >= 0 && box.right <= innerWidth + 1 && box.top >= 0 && box.bottom <= innerHeight + 1 && box.height > 40; })()`, "chat:model-contained");
  const modelScreenshot = await captureScreenshot(client, evidenceDir, `${prefix}-model-picker`);
  await client.evaluate(`document.querySelector('[data-composer-model-panel] input').focus()`);
  await key(client, "Escape", "Escape", 27);
  await client.waitFor(`!document.querySelector('[data-composer-model-panel]')`, "chat:model-escape");
  await client.waitFor(`document.activeElement === document.querySelector('[data-composer-model-trigger]')`, "chat:model-focus-return");
  await client.evaluate(`document.querySelector('[data-composer-model-trigger]').click()`);
  await client.waitFor(`Boolean(document.querySelector('[data-composer-model-option="model-b"]'))`, "chat:model-reopen");
  await client.evaluate(`document.querySelector('[data-composer-model-option="model-b"]').click()`);
  await client.waitFor(`document.querySelector('[data-composer-model-trigger]')?.textContent.includes('合成模型乙') && !document.querySelector('[data-composer-model-panel]')`, "chat:model-selected");
  return { geometry, activity, reveal, entryClearOfMessage, filesScreenshot, waitingScreenshot, stoppedScreenshot, messagesScreenshot, failureScreenshot, modelScreenshot, modelSelected: true, modelFocusReturn: true,
    suggestionDraft: true, hydratedAttachment: true, questionAnswers: true, mermaid: true, waitingPainted: true, richTextReachable: true, sendOnce: true, streamUpdated: true, stopConfirmed: true, cancellationRetainsText: true, synchronized: true, richText: true, safeFailure: true, copyVerified: true };
}
