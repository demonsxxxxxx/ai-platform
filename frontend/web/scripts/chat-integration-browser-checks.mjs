import assert from "node:assert/strict";
import { captureScreenshot, sleep } from "./browser-smoke-harness.mjs";

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
    const scroll = context.querySelector('[role=tabpanel]:not([hidden]) [data-profile-drive-tree-scroll]');
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
    assert.ok(geometry.context.width >= 280 && geometry.context.width <= 520);
  } else {
    assert.ok(geometry.popup && geometry.popup.x >= 0 && geometry.popup.right <= viewport.width + 1);
    assert.ok(geometry.popup.bottom <= viewport.height + 1 && geometry.focusedInside);
    await client.evaluate(`(() => { const buttons = [...document.querySelectorAll('[role=dialog] button')].filter(n => !n.disabled && n.getBoundingClientRect().width > 0); buttons.at(-1).focus(); })()`);
    await key(client, "Tab", "Tab", 9);
    await client.waitFor(`document.querySelector('[role=dialog]').contains(document.activeElement)`, "chat:drawer-tab-trap");
    await key(client, "Tab", "Tab", 9, 8);
    await client.waitFor(`document.querySelector('[role=dialog]').contains(document.activeElement)`, "chat:drawer-shift-tab-trap");
  }
  await client.evaluate(`(() => { document.querySelector('#profile-drive-profile-tab').focus(); document.querySelector('[role=tabpanel]:not([hidden]) [data-profile-drive-tree-scroll]').scrollTop = 0; })()`);
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
  await client.evaluate(`(() => { const input = document.querySelector('[data-slot="composer-input"] textarea'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, '保留普通草稿'); input.dispatchEvent(new Event('input', { bubbles: true })); window.__chatIntegration.attach(); })()`);
  await client.waitFor(`document.querySelector('[data-slot="composer-input"] textarea').value === '保留普通草稿'`, "chat:ordinary-draft-ready");
  await client.evaluate(`window.__chatIntegration.showQuestions()`);
  await client.waitFor(`Boolean(document.querySelector('[data-run-question-card]'))`, "chat:question-card");
  // Numbered steps: only the active question is on screen, advanced locally.
  await client.waitFor(`document.querySelector('[data-run-question-progress]')?.textContent.includes('第 1/3 题')`, "chat:question-step-1");
  const unifiedSurface = await client.evaluate(`(() => {
    const card = document.querySelector('[data-run-question-card]');
    const surface = card.closest('[data-slot="composer-bar"]');
    const ordinary = document.querySelector('[data-slot="composer-ordinary-controls"]');
    const slot = document.querySelector('[data-slot="composer-task-surface"]');
    const box = (node) => { const r = node.getBoundingClientRect(); return { x: r.x, right: r.right, width: r.width }; };
    return {
      insideComposer: Boolean(surface),
      borders: document.querySelectorAll('[data-slot="composer-bar"]').length,
      cardBox: box(card),
      surfaceBox: box(surface),
      ordinaryHidden: getComputedStyle(ordinary).display === 'none',
      shortQuestionScrolls: slot.scrollHeight > slot.clientHeight + 1,
      choicesVisible: [...card.querySelectorAll('[aria-pressed], [role="checkbox"]')].every(node => { const b = node.getBoundingClientRect(); const s = slot.getBoundingClientRect(); return b.top >= s.top && b.bottom <= s.bottom; }),
      stepFields: document.querySelectorAll('[data-run-question-index]').length,
      nextLabel: document.querySelector('[data-run-question-advance]')?.textContent?.trim(),
      targets: [...document.querySelectorAll('[data-run-question-card] button')].map((node) => Math.round(node.getBoundingClientRect().height)),
    };
  })()`);
  assert.ok(unifiedSurface.insideComposer, JSON.stringify(unifiedSurface));
  assert.equal(unifiedSurface.borders, 1, "task questions and composer share one bordered surface");
  assert.equal(unifiedSurface.stepFields, 1, "one active question at a time");
  assert.equal(unifiedSurface.nextLabel, "下一题");
  assert.ok(unifiedSurface.ordinaryHidden && !unifiedSurface.shortQuestionScrolls && unifiedSurface.choicesVisible, JSON.stringify(unifiedSurface));
  assert.ok(unifiedSurface.cardBox.right <= unifiedSurface.surfaceBox.right + 1 && unifiedSurface.cardBox.x >= unifiedSurface.surfaceBox.x - 1, JSON.stringify(unifiedSurface));
  assert.ok(unifiedSurface.targets.every((height) => height >= 40), JSON.stringify(unifiedSurface.targets));
  const questionScreenshots = [await captureScreenshot(client, evidenceDir, `${prefix}-question-step-1`)];
  // Real pointer events and hit testing: the action row must be painted, inside
  // the viewport and the topmost element at its centre, never a DOM .click().
  const pointerClick = async (selector) => {
    const hit = await client.evaluate(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return null;
      const r = el.getBoundingClientRect();
      const x = Math.round(r.left + r.width / 2), y = Math.round(r.top + r.height / 2);
      const top = document.elementFromPoint(x, y);
      return { x, y, height: Math.round(r.height), reachable: Boolean(top && (top === el || el.contains(top))),
        inViewport: x > 0 && x < innerWidth && y > 0 && y < innerHeight, topTag: top?.tagName ?? null };
    })()`);
    assert.ok(hit, `question control missing: ${selector}`);
    assert.ok(hit.reachable && hit.inViewport, `question control not pointer-reachable: ${selector} ${JSON.stringify(hit)}`);
    assert.ok(hit.height >= 40, `question control under 40px: ${selector} ${JSON.stringify(hit)}`);
    await client.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: hit.x, y: hit.y, button: "none" });
    await client.send("Input.dispatchMouseEvent", { type: "mousePressed", x: hit.x, y: hit.y, button: "left", clickCount: 1 });
    await client.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: hit.x, y: hit.y, button: "left", clickCount: 1 });
    await sleep(180);
    return hit;
  };
  const pressKey = async (key, code, vk, text) => {
    const base = { key, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk };
    await client.send("Input.dispatchKeyEvent", { type: text ? "keyDown" : "rawKeyDown", ...base, ...(text ? { text } : {}) });
    await client.send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
    await sleep(150);
  };
  for (const [step, labels] of [[1, ["普通"]], [2, ["PDF", "DOCX"]], [3, []]]) {
    if (step > 1) await pointerClick("[data-run-question-advance]");
    await client.waitFor(`document.querySelector('[data-run-question-progress]')?.textContent.includes('第 ${step}/3 题')`, `chat:question-step-${step}`);
    for (const label of labels) await pointerClick(`[data-run-question-card] [aria-label="${label}"]`);
    const actions = await client.evaluate(`(() => {
      const adv = document.querySelector('[data-run-question-advance]');
      const prev = document.querySelector('[data-run-question-previous]');
      const body = document.querySelector('[data-slot="composer-task-surface"]');
      const r = (n) => { const b = n.getBoundingClientRect(); return { top: Math.round(b.top), bottom: Math.round(b.bottom), h: Math.round(b.height) }; };
      return { adv: r(adv), prev: prev ? r(prev) : null, bodyScrolls: body.scrollHeight > body.clientHeight + 1,
        ordinaryHidden: getComputedStyle(document.querySelector('[data-slot="composer-ordinary-controls"]')).display === 'none',
        choices: [...document.querySelectorAll('[data-run-question-card] [aria-pressed], [data-run-question-card] [role="checkbox"]')].map(node => { const b = node.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, height: b.height }; }) };
    })()`);
    assert.ok(actions.adv.h >= 40 && actions.adv.top >= 0 && actions.adv.bottom <= viewport.height + 1, `step ${step} action row clipped ${JSON.stringify(actions)}`);
    assert.ok(!actions.bodyScrolls && actions.ordinaryHidden, `short step ${step} must fit without inner scrolling ${JSON.stringify(actions)}`);
    assert.ok(actions.choices.every(choice => choice.top >= 0 && choice.bottom <= viewport.height && choice.height >= 40), `step ${step} has a hidden option ${JSON.stringify(actions)}`);
    if (actions.prev) assert.ok(actions.prev.h >= 40, `step ${step} previous under 40px ${JSON.stringify(actions)}`);
    questionScreenshots.push(await captureScreenshot(client, evidenceDir, `${prefix}-question-step-${step}`));
    if (step === 1) {
      await pointerClick('[data-run-question-other-toggle]');
      await client.send('Input.insertText', { text: '其他优先级' });
      await pointerClick('[data-run-question-other-clear]');
      await pointerClick('[data-run-question-card] [aria-label="普通"]');
      await pointerClick('[data-run-question-advance]');
      await pointerClick('[data-run-question-previous]');
      await pointerClick('[data-run-question-other-toggle]');
      assert.equal(await client.evaluate("document.querySelector('[data-run-question-card] textarea').value"), '其他优先级');
      await pointerClick('[data-run-question-other-clear]');
      await pointerClick('[data-run-question-card] [aria-label="普通"]');
    }
  }
  await client.evaluate("document.querySelector('[data-run-question-card] textarea').focus()");
  await client.send("Input.insertText", { text: "请保留目录结构" });
  await sleep(150);
  await pressKey("Enter", "Enter", 13, String.fromCharCode(13));
  await client.waitFor(`Boolean(document.querySelector('[data-run-question-status="answered"]'))`, "chat:question-answered");
  const answerReceipt = await client.evaluate(`document.querySelector('[data-run-question-status="answered"]').textContent`);
  assert.match(answerReceipt, /答案已提交/);
  assert.doesNotMatch(answerReceipt, /问题已处理/);
  assert.deepEqual(await client.evaluate(`window.__chatIntegration.lastAnswers`), { priority: "normal", formats: ["pdf", "docx"], note: { text: "请保留目录结构" } });
  assert.equal(await client.evaluate("getComputedStyle(document.querySelector('[data-slot=\"composer-ordinary-controls\"]')).display !== 'none'"), true);
  assert.equal(await client.evaluate("document.querySelector('[data-slot=\"composer-input\"] textarea').value"), '保留普通草稿');
  assert.equal(await client.evaluate("Boolean(document.querySelector('[data-slot=\"composer-attachments\"] [data-composer-file-reference]'))"), true);
  assert.equal(await client.evaluate('window.__chatIntegration.sends'), 1, 'questions must not send the hidden draft');
  await client.evaluate("(() => { const input = document.querySelector('[data-slot=\"composer-input\"] textarea'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, ''); input.dispatchEvent(new Event('input', { bubbles: true })); })()");
  // Coexistence regression: long closed receipts and an open pending question share
  // the bounded task surface. Only the receipt region scrolls to its final sentinel;
  // the pending question body, its action row and the Composer stay usable.
  await client.evaluate("window.__chatIntegration.showQuestions()");
  await client.evaluate("window.__chatIntegration.showHistoryWithReceipts()");
  await client.waitFor(`document.querySelectorAll('[data-run-question-card]').length >= 1
    && Boolean(document.querySelector('[data-run-input-receipts]'))`, "chat:history-with-pending");
  await client.waitFor(`document.querySelector('[data-run-question-progress]')?.textContent.includes('第 1/3 题')`, "chat:history-pending-step");
  assert.equal(await client.evaluate("document.querySelector('[data-run-input-history-disclosure]').open"), false);
  const collapsedHistoryScreenshot = await captureScreenshot(client, evidenceDir, `${prefix}-history-collapsed`);
  await pointerClick('[data-run-input-history-toggle]');
  const coexistence = await client.evaluate(`(() => {
    const receipts = document.querySelector('[data-run-input-history-panel]');
    const current = document.querySelector('[data-run-question-card]');
    const adv = document.querySelector('[data-run-question-advance]');
    const bar = document.querySelector('[data-slot="composer-bar"]');
    const composerInput = document.querySelector('[data-slot="composer-input"] textarea');
    const r = (n) => { const b = n.getBoundingClientRect(); return { top: Math.round(b.top), bottom: Math.round(b.bottom), h: Math.round(b.height) }; };
    const hit = (n) => { const b = n.getBoundingClientRect(); const t = document.elementFromPoint(Math.round(b.left + b.width / 2), Math.round(b.top + b.height / 2)); return Boolean(t && (t === n || n.contains(t))); };
    return {
      receipts: r(receipts), receiptsScrollable: receipts.scrollHeight > receipts.clientHeight,
      receiptRegions: document.querySelectorAll('[data-run-input-receipts]').length,
      historyRuns: receipts.querySelectorAll('[data-run-input-history-run]').length,
      current: r(current), adv: r(adv), advReachable: hit(adv),
      ordinaryHidden: composerInput.getBoundingClientRect().height === 0,
      bodyScrollOwner: (() => { const b = document.querySelector('[data-run-question-index]').parentElement; return { sh: b.scrollHeight, ch: b.clientHeight, scrolls: b.scrollHeight > b.clientHeight }; })(),
      sentinel: document.body.textContent.includes('【回执末尾哨兵】'),
      bodyWidth: document.body.scrollWidth, viewportWidth: innerWidth,
    };
  })()`);
  assert.equal(coexistence.receiptRegions, 1, "all historical Runs share one scroll region");
  assert.equal(coexistence.historyRuns, 10, "pagination can accumulate many closed Runs");
  assert.ok(coexistence.receiptsScrollable, `receipt region must scroll ${JSON.stringify(coexistence)}`);
  assert.ok(coexistence.bodyScrollOwner.ch >= 40, `history must not squeeze the active question body away ${JSON.stringify(coexistence)}`);
  assert.ok(coexistence.advReachable && coexistence.adv.h >= 40, `pending action row unreachable ${JSON.stringify(coexistence)}`);
  assert.ok(coexistence.ordinaryHidden, `ordinary composer must stay hidden ${JSON.stringify(coexistence)}`);
  assert.ok(coexistence.bodyWidth <= coexistence.viewportWidth + 1, `horizontal overflow ${JSON.stringify(coexistence)}`);
  // Scroll the receipt region with a real wheel over its centre to read the sentinel.
  const receiptCentre = await client.evaluate(`(() => { const n = document.querySelector('[data-run-input-history-panel]'); const b = n.getBoundingClientRect(); return { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2), before: n.scrollTop }; })()`);
  await client.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: receiptCentre.x, y: receiptCentre.y, button: "none" });
  for (let tick = 0; tick < 80; tick += 1) {
    await client.send("Input.dispatchMouseEvent", { type: "mouseWheel", x: receiptCentre.x, y: receiptCentre.y, deltaX: 0, deltaY: 800 });
    await sleep(100);
    if (await client.evaluate("(() => { const n = document.querySelector('[data-run-input-history-panel]'); return n.scrollTop + n.clientHeight >= n.scrollHeight - 4; })()")) break;
  }
  await sleep(300);
  const scrolled = await client.evaluate(`(() => {
    const n = document.querySelector('[data-run-input-history-panel]');
    // The sentinel lives inside the long receipt text: measure the deepest element
    // whose own text ends with it, so the check does not rely on outer wrappers.
    const sentinel = [...n.querySelectorAll('*')].reverse().find((node) => (node.textContent || '').includes('【回执末尾哨兵】'));
    const visible = sentinel ? (() => { const sb = sentinel.getBoundingClientRect(); const nb = n.getBoundingClientRect(); return sb.bottom > nb.top && sb.top < nb.bottom && sb.height > 0; })() : false;
    const atEnd = n.scrollTop + n.clientHeight >= n.scrollHeight - 4;
    return { after: n.scrollTop, max: n.scrollHeight - n.clientHeight, moved: n.scrollTop > 0, atEnd, visible,
      sentinelTag: sentinel?.tagName ?? null,
      advReachable: (() => { const a = document.querySelector('[data-run-question-advance]'); const b = a.getBoundingClientRect(); const t = document.elementFromPoint(Math.round(b.left + b.width/2), Math.round(b.top + b.height/2)); return Boolean(t && (t === a || a.contains(t))); })() };
  })()`);
  assert.ok(scrolled.moved && scrolled.atEnd && scrolled.visible, `receipt wheel scroll must reach the last Run's end ${JSON.stringify(scrolled)}`);
  assert.ok(scrolled.advReachable, `action row must stay reachable after scrolling receipts ${JSON.stringify(scrolled)}`);
  const coexistenceScreenshot = await captureScreenshot(client, evidenceDir, `${prefix}-history-with-pending`);
  // History-only state: with no open question, the receipt area is the sole scroll
  // owner and long closed receipts stay fully readable.
  await client.evaluate("window.__chatIntegration.clearQuestion()");
  await client.waitFor(`!document.querySelector('[data-run-question-card]')
    && Boolean(document.querySelector('[data-run-input-receipts]'))`, "chat:history-only");
  await client.waitFor("!document.querySelector('[data-run-input-history-disclosure]').open", 'chat:history-collapse-after-close');
  await pointerClick('[data-run-input-history-toggle]');
  const historyOnlyLayout = await client.evaluate(`(() => {
    const n = document.querySelector('[data-run-input-history-panel]');
    const composer = document.querySelector('[data-slot="composer-input"] textarea');
    const b = n.getBoundingClientRect();
    const t = document.elementFromPoint(Math.round(b.left + b.width / 2), Math.round(b.top + Math.min(20, b.height / 2)));
    return { overflowY: getComputedStyle(n).overflowY, scrolls: n.scrollHeight > n.clientHeight,
      regions: document.querySelectorAll('[data-run-input-receipts]').length,
      runs: n.querySelectorAll('[data-run-input-history-run]').length,
      containsPointer: Boolean(t && (t === n || n.contains(t))),
      composerReachable: (() => { const cb = composer.getBoundingClientRect(); const h = document.elementFromPoint(Math.round(cb.left + cb.width/2), Math.round(cb.top + cb.height/2)); return Boolean(h && (h === composer || composer.contains(h))); })(),
      bodyWidth: document.body.scrollWidth, viewportWidth: innerWidth };
  })()`);
  assert.equal(historyOnlyLayout.regions, 1);
  assert.equal(historyOnlyLayout.runs, 10);
  assert.ok(historyOnlyLayout.scrolls && historyOnlyLayout.overflowY === "auto", `history-only receipts must scroll ${JSON.stringify(historyOnlyLayout)}`);
  assert.ok(historyOnlyLayout.containsPointer, `history-only receipts must be the pointer owner ${JSON.stringify(historyOnlyLayout)}`);
  assert.ok(historyOnlyLayout.composerReachable, `ordinary composer must remain usable when history is expanded ${JSON.stringify(historyOnlyLayout)}`);
  assert.ok(historyOnlyLayout.bodyWidth <= historyOnlyLayout.viewportWidth + 1, `history-only horizontal overflow ${JSON.stringify(historyOnlyLayout)}`);
  const historyOnlyScreenshot = await captureScreenshot(client, evidenceDir, `${prefix}-history-only`);
  await client.evaluate("window.__chatIntegration.showQuestions()");
  await client.waitFor("Boolean(document.querySelector('[data-run-question-card]'))", "chat:question-restored");
  await client.evaluate("document.querySelector('[data-run-input-history-panel]').scrollTop = 0");
  await client.evaluate("window.__chatIntegration.clearHistory()");
  await client.evaluate('window.__chatIntegration.showLongQuestion()');
  await client.waitFor("document.querySelector('[data-run-question-card]')?.textContent.includes('长问题')", 'chat:long-question');
  const longQuestion = await client.evaluate(`(() => {
    const n = document.querySelector('[data-slot="composer-task-surface"]'); const b = n.getBoundingClientRect();
    return { scrolls: n.scrollHeight > n.clientHeight, height: b.height, top: b.top, bottom: b.bottom, x: b.left + b.width / 2, y: b.top + Math.min(60, b.height / 2) };
  })()`);
  assert.ok(longQuestion.scrolls && longQuestion.height > 100 && longQuestion.top >= 0 && longQuestion.bottom <= viewport.height, JSON.stringify(longQuestion));
  await client.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: longQuestion.x, y: longQuestion.y, button: 'none' });
  for (let tick = 0; tick < 5; tick += 1) {
    await client.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: longQuestion.x, y: longQuestion.y, deltaX: 0, deltaY: 800 });
    await sleep(120);
  }
  await pointerClick('[data-run-question-other-toggle]');
  await client.send('Input.insertText', { text: '长题其他答复' });
  await client.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: longQuestion.x, y: longQuestion.y, deltaX: 0, deltaY: 800 });
  await sleep(200);
  const longQuestionScreenshot = await captureScreenshot(client, evidenceDir, `${prefix}-long-question`);
  await pointerClick('[data-run-question-advance]');
  await client.waitFor("!document.querySelector('[data-question-mode]')", 'chat:long-question-accepted');
  await client.evaluate(`window.__chatIntegration.resolveQuestions()`);
  await client.waitFor(`!document.querySelector('[data-run-question-status]')`, "chat:question-resolved");
  await client.evaluate('window.__chatIntegration.showQuestions()');
  await client.waitFor("Boolean(document.querySelector('[data-run-question-stop]'))", 'chat:question-stop');
  await pointerClick('[data-run-question-stop]');
  await client.waitFor(`Boolean(document.querySelector('[role=dialog]'))`, "chat:stop-confirm");
  assert.equal(await client.evaluate(`window.__chatIntegration.stops`), 0);
  await client.evaluate(`([...document.querySelectorAll('[role=dialog] button')].find(node => node.textContent.trim() === '取消')).click()`);
  await client.waitFor(`!document.querySelector('[role=dialog]')`, "chat:stop-dismissed");
  assert.equal(await client.evaluate(`window.__chatIntegration.stops`), 0);
  await pointerClick('[data-run-question-stop]');
  await client.waitFor(`Boolean(document.querySelector('[role=dialog]'))`, "chat:stop-reopen");
  await client.evaluate(`([...document.querySelectorAll('[role=dialog] button')].find(node => node.textContent.trim() === '停止生成')).click()`);
  await client.waitFor(`window.__chatIntegration.stops === 1 && document.body.textContent.includes('生成已取消') && !document.querySelector('[role=dialog]')`, "chat:stop-acknowledged");
  assert.ok(await client.evaluate(`document.querySelector('[data-message-id="assistant-synthetic-1"]').textContent.includes('正在整理文件。')`));
  await client.evaluate('window.__chatIntegration.clearQuestion()');
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
  return { geometry, activity, reveal, entryClearOfMessage, unifiedSurface, collapsedHistoryScreenshot, longQuestion, longQuestionScreenshot, coexistence, receiptScroll: scrolled, historyOnlyLayout, coexistenceScreenshot, historyOnlyScreenshot, filesScreenshot, waitingScreenshot, stoppedScreenshot, messagesScreenshot, failureScreenshot, modelScreenshot, questionScreenshots,
    modelSelected: true, modelFocusReturn: true, suggestionDraft: true, hydratedAttachment: true, questionAnswers: true, mermaid: true, waitingPainted: true, richTextReachable: true,
    sendOnce: true, streamUpdated: true, stopConfirmed: true, cancellationRetainsText: true, synchronized: true, richText: true, safeFailure: true, copyVerified: true };
}
