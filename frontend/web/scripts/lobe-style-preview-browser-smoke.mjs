#!/usr/bin/env node
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { captureScreenshot, sleep, startBrowser } from "./browser-smoke-harness.mjs";

const baseUrl = (process.env.AI_PLATFORM_FRONTEND_URL || "http://127.0.0.1:5186").replace(/\/+$/, "");
const outputDir = resolve(process.env.AI_PLATFORM_STYLE_PREVIEW_DIR || "../../.codex-tmp/lobe-style-preview");
mkdirSync(outputDir, { recursive: true });
const results = [];

async function click(client, selector, label) {
  const point = await client.evaluate(`(() => {
    const element = [...document.querySelectorAll(${JSON.stringify(selector)})].find(e => e.checkVisibility() ${label ? `&& e.innerText.trim() === ${JSON.stringify(label)}` : ""});
    if (!element) throw new Error('preview_control_missing');
    element.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    window.__previewTrigger = element;
    const r = element.getBoundingClientRect();
    return {x:r.x+r.width/2,y:r.y+r.height/2};
  })()`);
  await client.send("Input.dispatchMouseEvent", { type: "mousePressed", button: "left", clickCount: 1, ...point });
  await client.send("Input.dispatchMouseEvent", { type: "mouseReleased", button: "left", clickCount: 1, ...point });
}

async function escape(client) {
  await client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await client.waitFor("![...document.querySelectorAll('[role=dialog]')].some(e=>e.checkVisibility())", "drawer closed");
  await client.waitFor("document.activeElement === window.__previewTrigger", "drawer focus returned");
}

for (const viewport of [
  { name: "desktop", width: 1440, height: 900, mobile: false },
  { name: "phone", width: 390, height: 844, mobile: true },
]) {
  const browser = await startBrowser({ viewport, timeoutMs: 15000, profilePrefix: "lobe-style-preview-" });
  let scriptId;
  try {
    for (const theme of ["light", "dark"]) {
      if (scriptId) await browser.client.send("Page.removeScriptToEvaluateOnNewDocument", { identifier: scriptId });
      scriptId = (await browser.client.send("Page.addScriptToEvaluateOnNewDocument", { source: `
        localStorage.setItem('ai-platform-theme', ${JSON.stringify(theme)});
        window.__previewEvidence = { errors: [], api: [] };
        window.addEventListener('error', event => window.__previewEvidence.errors.push(String(event.message)));
        window.addEventListener('unhandledrejection', event => window.__previewEvidence.errors.push(String(event.reason)));
        const originalFetch = window.fetch;
        window.fetch = (input, init) => {
          const url = new URL(typeof input === 'string' ? input : input.url, location.href);
          if (url.pathname.startsWith('/api/')) {
            window.__previewEvidence.api.push(url.pathname);
            return Promise.reject(new Error('style_preview_business_request'));
          }
          return originalFetch(input, init);
        };
      ` })).identifier;
      for (const view of ["market", "models", "chat"]) {
        await browser.client.send("Page.navigate", { url: `${baseUrl}/style-preview.html?view=${view}` });
        await browser.client.waitFor(`Boolean(document.querySelector('[data-preview-${view}]'))`, `${view} mounted`);
        await browser.client.evaluate("document.fonts.ready.then(() => true)");
        await sleep(500);
        const evidence = await browser.client.evaluate(`(() => ({
          width: innerWidth, theme: document.querySelector('[data-preview-theme]').dataset.previewTheme,
          overflow: document.documentElement.scrollWidth > innerWidth,
          tinyIcons: [...document.querySelectorAll('button svg')].filter(e=>e.checkVisibility() && e.getBoundingClientRect().width < 10).map(e=>e.getBoundingClientRect().width),
          api: window.__previewEvidence.api, errors: window.__previewEvidence.errors,
          cardsFit: [...document.querySelectorAll('[data-preview-expert]')].every(e=>e.getBoundingClientRect().right <= innerWidth),
          tableScrolls: [...document.querySelectorAll('[data-preview-models] div')].some(e=>getComputedStyle(e).overflowX === 'auto' && e.scrollWidth > e.clientWidth)
        }))()`);
        assert.equal(evidence.width, viewport.width);
        assert.equal(evidence.theme, theme);
        assert.equal(evidence.overflow, false, `${view}: document overflow`);
        assert.deepEqual(evidence.tinyIcons, [], `${view}: crushed icons`);
        assert.deepEqual(evidence.errors, [], `${view}: render errors`);
        assert.deepEqual(evidence.api, [], `${view}: business request`);
        assert.equal(evidence.cardsFit, true);
        if (viewport.mobile && view === "models") {
          assert.equal(evidence.tableScrolls, true, "table must scroll instead of crushing columns");
          assert.equal(await browser.client.evaluate("[...document.querySelectorAll('button')].find(e => e.innerText === '保存修改').getBoundingClientRect().height >= 40"), true, "primary touch action is at least 40px");
        }
        const screenshot = await captureScreenshot(browser.client, outputDir, `${view}-${viewport.name}-${theme}`);
        results.push({ view, viewport: viewport.name, theme, screenshot, ...evidence });

        if (view === "models") {
          await click(browser.client, "button", "连接设置");
          await browser.client.waitFor("[...document.querySelectorAll('[role=dialog]')].some(e=>e.checkVisibility())", "connection drawer");
          await sleep(400);
          assert.equal(await browser.client.evaluate("[...document.querySelectorAll('[role=dialog]')].filter(e=>e.checkVisibility()).every(e=>e.getBoundingClientRect().left >= -1 && e.getBoundingClientRect().right <= innerWidth + 1)"), true, "drawer fits viewport");
          await captureScreenshot(browser.client, outputDir, `drawer-${viewport.name}-${theme}`);
          await escape(browser.client);
        }
        if (theme !== "light") continue;
        if (view === "market") {
          await browser.client.evaluate("document.querySelector('input[aria-label=\"搜索专家\"]').focus()");
          await browser.client.send("Input.insertText", { text: "质量合规" });
          await browser.client.waitFor("document.querySelectorAll('[data-preview-expert]').length === 1", "market filtered");
          await click(browser.client, 'button[aria-label="收藏质量合规专家"]');
          await browser.client.waitFor("document.querySelector('button[aria-label=\"取消收藏质量合规专家\"]')?.getAttribute('aria-pressed') === 'true'", "favorite updated");
          if (viewport.mobile) {
            await click(browser.client, 'button[aria-label="打开导航"]');
            await browser.client.waitFor("[...document.querySelectorAll('[role=dialog]')].some(e=>e.checkVisibility())", "navigation drawer");
            await escape(browser.client);
          }
          await click(browser.client, 'button[aria-label="切换明暗主题"]');
          await browser.client.waitFor("document.querySelector('[data-preview-theme]').dataset.previewTheme === 'dark'", "theme toggled");
        } else if (view === "models") {
          assert.equal(await browser.client.evaluate("[...document.querySelector('#preview-model-qwen').labels].some(label => label.textContent === '启用 Qwen3')"), true, "switch has an associated accessible label");
          await click(browser.client, '#preview-model-qwen');
          await browser.client.waitFor("document.querySelector('[role=status]').innerText.includes('未保存')", "model edited");
          await click(browser.client, "button", "保存修改");
          await browser.client.waitFor("document.querySelector('[role=status]').innerText.includes('4 个模型已启用') && document.querySelector('[role=status]').innerText.includes('已保存')", "sample saved");
        } else {
          await browser.client.evaluate("document.querySelector('textarea[aria-label=\"输入示例消息\"]').focus()");
          await browser.client.send("Input.insertText", { text: "请整理成复核清单。" });
          await click(browser.client, 'button[aria-label="发送示例消息"]');
          await browser.client.waitFor("document.querySelector('[data-preview-local-message]')?.textContent.includes('请整理成复核清单。') && document.querySelector('textarea').value === ''", "local message submitted");
        }
        assert.deepEqual(await browser.client.evaluate("window.__previewEvidence"), { errors: [], api: [] });
        console.log(`${view} ${viewport.name} ${theme}: render and interactions passed`);
      }
    }
  } catch (error) {
    const diagnostic = await browser.client.evaluate("({url:location.href,heading:document.querySelector('h1')?.textContent,evidence:window.__previewEvidence})").catch(() => null);
    writeFileSync(resolve(outputDir, `failure-${viewport.name}.json`), JSON.stringify({ error: String(error), diagnostic }, null, 2));
    throw error;
  } finally {
    await browser.close();
  }
}
writeFileSync(resolve(outputDir, "evidence.json"), JSON.stringify(results, null, 2));
console.log(`Passed ${results.length} rendered states; screenshots: ${outputDir}`);
