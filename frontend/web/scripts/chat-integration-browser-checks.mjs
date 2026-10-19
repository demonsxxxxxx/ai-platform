import assert from "node:assert/strict";
import { captureScreenshot } from "./browser-smoke-harness.mjs";

async function key(client, key, code, virtualKey, modifiers = 0) {
  for (const type of ["keyDown", "keyUp"]) {
    await client.send("Input.dispatchKeyEvent", { type, key, code, windowsVirtualKeyCode: virtualKey, modifiers });
  }
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
  await client.waitFor(`document.querySelectorAll('[data-chat-activity-status]').length >= 4`, "chat:tool-statuses");
  const entryClearOfMessage = await client.evaluate(`(() => {
    const toggle = document.querySelector('[data-librechat-context-toggle]').getBoundingClientRect();
    const message = document.querySelector('[data-message-id="user-synthetic"]').getBoundingClientRect();
    return toggle.bottom <= message.top || toggle.left >= message.right || toggle.right <= message.left;
  })()`);
  assert.ok(entryClearOfMessage, "file entry must not overlap the first user message");
  const activity = await client.evaluate(`([...document.querySelectorAll('[data-chat-activity-status]')]).map(node => {
    const style = getComputedStyle(node.firstElementChild); const row = node.firstElementChild.firstElementChild;
    return { status: node.dataset.chatActivityStatus, color: style.color, background: style.backgroundColor, fontSize: parseFloat(getComputedStyle(row).fontSize), clickable: Boolean(node.querySelector('button')) };
  })`);
  for (const status of ["success", "error", "cancelled", "loading"]) {
    const row = activity.find(item => item.status === status);
    assert.ok(row && row.background !== "rgba(0, 0, 0, 0)" && row.fontSize >= 13 && !row.clickable, JSON.stringify(activity));
  }
  assert.notEqual(activity.find(item => item.status === "success").color, activity.find(item => item.status === "error").color);
  assert.equal(await client.evaluate(`document.querySelector('textarea').value`), "");
  await client.evaluate(`window.__chatIntegration.advance()`);
  await client.waitFor(`document.body.textContent.includes('正在整理文件。')`, "chat:stream-update");
  await client.evaluate(`document.querySelector('button[aria-label="停止生成"]').click()`);
  await client.waitFor(`Boolean(document.querySelector('[role=dialog]'))`, "chat:stop-confirm");
  assert.equal(await client.evaluate(`window.__chatIntegration.stops`), 0);
  await client.evaluate(`([...document.querySelectorAll('[role=dialog] button')].find(node => node.textContent.trim() === '取消')).click()`);
  await client.waitFor(`!document.querySelector('[role=dialog]')`, "chat:stop-dismissed");
  assert.equal(await client.evaluate(`window.__chatIntegration.stops`), 0);
  await client.evaluate(`document.querySelector('button[aria-label="停止生成"]').click()`);
  await client.waitFor(`Boolean(document.querySelector('[role=dialog]'))`, "chat:stop-reopen");
  await client.evaluate(`([...document.querySelectorAll('[role=dialog] button')].find(node => node.textContent.trim() === '停止生成')).click()`);
  await client.waitFor(`window.__chatIntegration.stops === 1 && document.body.textContent.includes('已整理文件。最终公开回答。') && !document.querySelector('[role=dialog]')`, "chat:stop-acknowledged");
  await client.evaluate(`([...document.querySelectorAll('[data-assistant-ui-message-actions] button[aria-label="复制"]')].at(-1)).click()`);
  await client.waitFor(`window.__chatCopies.at(-1) === '已整理文件。最终公开回答。' && Boolean(document.querySelector('[data-copied=true]'))`, "chat:native-copy");
  assert.equal(await client.evaluate(`document.body.textContent.includes('private executor name')`), false);
  await client.evaluate(`(() => { const toggle = [...document.querySelectorAll('button')].find(node => node.textContent.includes('全部展开')); if (!toggle) throw new Error('chat_activity_toggle_missing'); toggle.click(); })()`);
  await client.waitFor(`([...document.querySelectorAll('[data-chat-activity-status]')]).every(node => node.getBoundingClientRect().height > 0)`, "chat:activity-expanded");
  const messagesScreenshot = await captureScreenshot(client, evidenceDir, `${prefix}-messages`);
  return { geometry, activity, entryClearOfMessage, filesScreenshot, messagesScreenshot, suggestionDraft: true, sendOnce: true, streamUpdated: true, stopConfirmed: true, copyVerified: true };
}
