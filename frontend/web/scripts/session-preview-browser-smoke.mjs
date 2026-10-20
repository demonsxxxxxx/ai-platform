#!/usr/bin/env node
// Bounded acceptance of the session/sidebar/diagram changes, not a general UI suite.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { createServer } from "vite";
import { captureScreenshot, startBrowser } from "./browser-smoke-harness.mjs";

const evidenceDir = resolve("../../.codex-tmp/session-preview-browser-smoke");
const labels = JSON.parse(readFileSync(new URL("../src/i18n/locales/zh.json", import.meta.url), "utf8"));
const commit = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
const sourceFiles = ["scripts/session-preview-browser-smoke.mjs", "scripts/fixtures/session-preview.tsx", "scripts/fixtures/session-preview.html", "scripts/tsconfig.session-preview.json"];
const sourceHashes = Object.fromEntries(sourceFiles.map(file => [file, createHash("sha256").update(readFileSync(file)).digest("hex")]));
const dirty = Boolean(execFileSync("git", ["status", "--porcelain", "--untracked-files=normal"], { encoding: "utf8" }).trim());
const log = [];
const results = [];
const failures = [];
mkdirSync(evidenceDir, { recursive: true });
let server;
const viewports = [{ name: "desktop", width: 1440, height: 900, mobile: false }, { name: "mobile", width: 390, height: 844, mobile: true }];
const quote = JSON.stringify;
try {
  server = await createServer({ plugins: [{ name: "synthetic-disable-backend-proxy", configResolved(config) { config.server.proxy = undefined; } }], server: { host: "127.0.0.1", port: 4173, strictPort: true, open: false } });
  await server.listen();
  for (const theme of ["light", "dark"]) for (const viewport of viewports) {
    let browser;
    const prefix = `${theme}-${viewport.name}`;
    const screenshots = [];
    const measurements = {};
    try {
      browser = await startBrowser({ viewport, report: (...items) => log.push(JSON.stringify(items)) });
      const c = browser.client;
      const networkViolations = [];
      // Intercept before the first fixture navigation: includes XHR, beacon and resources.
      c.ws.addEventListener("message", event => {
        const message = JSON.parse(event.data);
        if (message.method !== "Fetch.requestPaused") return;
        const { requestId, request } = message.params;
        const url = new URL(request.url);
        const allowed = url.origin === "http://127.0.0.1:4173" && !url.pathname.startsWith("/api") && request.method === "GET";
        if (!allowed) networkViolations.push(`${request.method}:${url.origin}${url.pathname}`);
        c.send(allowed ? "Fetch.continueRequest" : "Fetch.failRequest", allowed ? { requestId } : { requestId, errorReason: "BlockedByClient" }).catch(error => networkViolations.push(String(error)));
      });
      await c.send("Fetch.enable", { patterns: [{ urlPattern: "*", requestStage: "Request" }] });
      const assert = async (expression, name) => {
        if (!(await c.evaluate(expression))) throw new Error(`assertion_failed:${name}`);
      };
      const wait = (expression, name) => c.waitFor(expression, name);
      const screenshot = async name => screenshots.push(await captureScreenshot(c, evidenceDir, `${prefix}-${name}`));
      const navigate = async (mode, collapsed = false) => {
        await c.send("Page.navigate", { url: `http://127.0.0.1:4173/scripts/fixtures/session-preview.html?theme=${theme}&mode=${mode}&collapsed=${collapsed}` });
        await wait("Boolean(window.__sessionPreview) && Boolean(document.querySelector('[data-synthetic-auth-ready=synthetic-user]'))", "fixture_ready");
        await assert(`document.documentElement.classList.contains('dark') === ${theme === "dark"}`, "theme");
      };
      // Use browser input rather than React callback extraction or synthetic DOM click events.
      const click = async expression => {
        const point = await c.evaluate(`(() => { const e = ${expression}; if (!e) throw Error('control_missing'); const r=e.getBoundingClientRect(); if(!r.width || !r.height) throw Error('control_not_visible'); return {x:r.left+r.width/2,y:r.top+r.height/2}; })()`);
        await c.send("Input.dispatchMouseEvent", { type: "mousePressed", button: "left", clickCount: 1, ...point });
        await c.send("Input.dispatchMouseEvent", { type: "mouseReleased", button: "left", clickCount: 1, ...point });
      };
      const key = async (key, code, windowsVirtualKeyCode, modifiers = 0) => {
        await c.send("Input.dispatchKeyEvent", { type: "keyDown", key, code, windowsVirtualKeyCode, modifiers });
        await c.send("Input.dispatchKeyEvent", { type: "keyUp", key, code, windowsVirtualKeyCode, modifiers });
      };
      const buttonText = (text, scope = "document") => `[...${scope}.querySelectorAll('button')].find(e=>e.textContent.trim()===${quote(text)})`;
      const dialog = "document.querySelector('[role=dialog]')";
      const contained = `(() => { const r=${dialog}.getBoundingClientRect(); return r.left>=0 && r.right<=innerWidth+1 && r.top>=0 && r.bottom<=innerHeight+1; })()`;
      const noErrors = async () => { await assert("window.__sessionPreview.errors.length === 0", "no_unstubbed_network_or_runtime_errors"); if (networkViolations.length) throw Error(`blocked_network:${JSON.stringify(networkViolations)}`); };
      // The real preference owner feeds the loaded sidebar; the skeleton independently reads storage.
      for (const collapsed of [false, true]) {
        await navigate("skeleton", collapsed);
        const skeletonWidth = await c.evaluate("document.querySelector('[data-synthetic-skeleton] > div').getBoundingClientRect().width");
        await screenshot(`skeleton-${collapsed ? "collapsed" : "expanded"}`);
        await noErrors();
        await navigate("sidebar", collapsed);
        await wait("Boolean(document.querySelector('[data-librechat-desktop-sidebar]'))", "sidebar_ready");
        if (viewport.mobile) await wait("document.querySelector('[data-librechat-mobile-sidebar]').getBoundingClientRect().left >= 0", "mobile_sidebar_painted");
        const sidebarWidth = await c.evaluate("document.querySelector('[data-librechat-desktop-sidebar]').getBoundingClientRect().width");
        if (viewport.mobile ? skeletonWidth !== 0 || sidebarWidth !== 0 : Math.abs(skeletonWidth - sidebarWidth) > 1) throw Error(`preference_width_mismatch:${skeletonWidth}:${sidebarWidth}`);
        await screenshot(`sidebar-${collapsed ? "collapsed" : "expanded"}`);
        await noErrors();
      }
      await navigate("diagram");
      await wait("Boolean(document.querySelector('[data-synthetic-diagram] div[style*=\"transform:\"] > svg'))", "excalidraw_export");
      const titleButton = text => `document.querySelector('button[title='+${quote(quote(text))}+']')`;
      await assert("(() => { const svg=document.querySelector('[data-synthetic-diagram] div[style*=\"transform:\"] > svg'); return svg.viewBox.baseVal.width>=1600 && svg.viewBox.baseVal.height>=900; })()", "real_export_not_toolbar_icon");
      const originalZoom = await c.evaluate("document.querySelector('[data-synthetic-diagram] div[style*=\"transform:\"] > svg').parentElement.style.transform");
      await click(titleButton(labels.documents.zoomIn));
      await assert(`document.querySelector('[data-synthetic-diagram] div[style*=\\\"transform:\\\"] > svg').parentElement.style.transform !== ${quote(originalZoom)}`, "zoom_changes_real_diagram");
      await click(titleButton(labels.documents.fitToScreen));
      await wait("document.querySelector('[data-synthetic-diagram] div[style*=\"transform:\"] > svg').parentElement.getAnimations().every(a=>a.playState!=='running')", "fit_transition");
      const firstFit = await c.evaluate("document.querySelector('[data-synthetic-diagram] div[style*=\"transform:\"] > svg').parentElement.style.transform");
      if (!firstFit.includes("scale(") || firstFit === "") throw Error("missing_real_fit_transform");
      await click(titleButton(labels.documents.zoomOut));
      await click(titleButton(labels.documents.fitToScreen));
      await wait("document.querySelector('[data-synthetic-diagram] div[style*=\"transform:\"] > svg').parentElement.getAnimations().every(a=>a.playState!=='running')", "second_fit_transition");
      await assert(`document.querySelector('[data-synthetic-diagram] div[style*=\"transform:\"] > svg').parentElement.style.transform === ${quote(firstFit)}`, "fit_independent_of_prior_zoom");
      await assert("(() => { const svg=document.querySelector('[data-synthetic-diagram] div[style*=\"transform:\"] > svg'); const r=svg.getBoundingClientRect(), p=svg.parentElement.parentElement.getBoundingClientRect(); return r.width>0 && r.height>0 && r.left>=p.left-1 && r.right<=p.right+1 && r.top>=p.top-1 && r.bottom<=p.bottom+1; })()", "diagram_fits_visible_container");
      measurements.zoomControls = await c.evaluate(`(() => { const names=${quote([labels.documents.zoomOut, labels.documents.zoomIn])}; return names.map(name => { const button=[...document.querySelectorAll('[data-synthetic-diagram] button')].find(e=>e.title===name); const b=button.getBoundingClientRect(), s=button.querySelector('svg').getBoundingClientRect(); return { name, buttonWidth:b.width, buttonHeight:b.height, iconWidth:s.width, iconHeight:s.height }; }); })()`);
      if (measurements.zoomControls.some(control => Math.abs(control.iconWidth - 16) > 1 || Math.abs(control.iconHeight - 16) > 1)) throw Error("zoom_icon_declared_size_not_rendered");
      await screenshot("diagram-fit");
      await noErrors();
      await navigate("sidebar");
      const sidebar = viewport.mobile ? "document.querySelector('[data-librechat-mobile-sidebar]')" : "document.querySelector('[data-librechat-desktop-sidebar]')";
      const openSidebar = async () => {
        if (viewport.mobile && await c.evaluate(`${sidebar}.classList.contains('-translate-x-full')`)) {
          await wait(`${sidebar}.getBoundingClientRect().right <= 1`, "mobile_sidebar_closed");
          await click("document.querySelector('[data-synthetic-open-sidebar]')");
        }
        if (viewport.mobile) await wait(`${sidebar}.getBoundingClientRect().left >= 0`, "mobile_sidebar_open");
      };
      await openSidebar();
      await wait(`${sidebar}.querySelectorAll('[data-slot="thread-list-item"]').length === 2`, "session_rows");
      const searchButton = `[...${sidebar}.querySelectorAll('button')].find(e=>e.textContent.includes(${quote(labels.sidebar.searchSessions)}))`;
      await click(searchButton);
      await wait("window.__sessionPreview.searches.length===1 && document.activeElement.tagName==='INPUT'", "search_initial_focus");
      await assert(contained, "search_portal_contained");
      await c.evaluate("window.__sessionPreview.resolveSearch(0,'initial')");
      await wait("document.body.textContent.includes('Synthetic session initial')", "initial_search_result");
      await c.send("Input.insertText", { text: "alpha" });
      await wait("window.__sessionPreview.searches.length===2", "debounced_alpha");
      await key("a", "KeyA", 65, 2);
      await c.send("Input.insertText", { text: "beta" });
      await wait("window.__sessionPreview.searches.length===3", "debounced_beta");
      await c.evaluate("window.__sessionPreview.resolveSearch(2,'beta-result'); window.__sessionPreview.resolveSearch(1,'stale-alpha')");
      await wait("document.body.textContent.includes('Synthetic session beta-result')", "current_result");
      await assert("!document.body.textContent.includes('stale-alpha')", "stale_search_cannot_replace_current");
      await wait("[...document.querySelectorAll('[role=dialog] [class*=grid-area]')].every(e => e.getAnimations().every(a => a.playState !== 'running')) && document.querySelector('[role=dialog] [class*=grid-area]').classList.contains('opacity-0')", "search_crossfade_finished");
      await screenshot("search-result");
      await key("ArrowDown", "ArrowDown", 40);
      await wait("[...document.querySelectorAll('[role=dialog] button')].some(e => e.textContent.includes('Synthetic session beta-result') && e.classList.contains('bg-[var(--theme-primary-light)]'))", "search_keyboard_highlight");
      await key("Enter", "Enter", 13);
      await wait("window.__sessionPreview.selected==='beta-result'", "search_keyboard_selection");
      if (await c.evaluate(`Boolean(${dialog})`)) await key("Escape", "Escape", 27);
      await wait(`!${dialog}`, "search_closed");
      // Open again and Escape while a receipt is outstanding.
      await openSidebar();
      await click(searchButton);
      await wait("window.__sessionPreview.searches.length===4", "search_reopen");
      await key("Escape", "Escape", 27);
      await c.evaluate("window.__sessionPreview.resolveSearch(3,'closed-stale')");
      await wait(`!${dialog}`, "search_escape");
      if (!viewport.mobile) await assert(`document.activeElement === ${searchButton}`, "search_focus_restored");
      await assert("!document.body.textContent.includes('closed-stale')", "closed_receipt_ignored");
      const openDelete = async () => {
        await openSidebar();
        const row = `[...${sidebar}.querySelectorAll('[data-slot="thread-list-item"]')].find(e=>e.textContent.includes('Synthetic session a'))`;
        await c.evaluate(`window.__sessionPreview.deleteAnchor = ${row}.querySelectorAll('button')[1]`);
        await click(`${row}.querySelectorAll('button')[1]`);
        if (viewport.mobile) {
          // SessionMenu intentionally renders a bottom Drawer with Buttons on mobile.
          await wait(`Boolean(${dialog}) && ${dialog}.textContent.includes(${quote(labels.sidebar.sessionOptions)})`, "session_actions_drawer");
          const remove = buttonText(labels.common.delete, dialog);
          await wait(`(() => { const e=${remove}; if (!e) return false; const r=e.getBoundingClientRect(); return r.top>=0 && r.bottom<=innerHeight && e.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)) && ${dialog}.getAnimations({subtree:true}).every(a=>a.playState!=='running'); })()`, "session_actions_drawer_ready");
          await click(remove);
        } else {
          await wait("Boolean(document.querySelector('[role=menuitem]'))", "session_menu");
          await c.evaluate(`window.__sessionPreview.deleteMenuItem = [...document.querySelectorAll('[role=menuitem]')].find(e=>e.textContent.trim()===${quote(labels.common.delete)})`);
          await click(`[...document.querySelectorAll('[role=menuitem]')].find(e=>e.textContent.trim()===${quote(labels.common.delete)})`);
        }
        await wait(`document.querySelectorAll('[role=dialog]').length===1 && ${dialog}.textContent.includes(${quote(labels.sidebar.deleteSession)})`, "delete_dialog");
        await assert(contained, "delete_portal_contained");
      };
      await openDelete();
      await screenshot("delete-confirm");
      await wait(`document.activeElement === ${buttonText(labels.common.delete, dialog)}`, "delete_initial_focus");
      await key("Tab", "Tab", 9);
      await wait(`document.activeElement === ${buttonText(labels.common.cancel, dialog)}`, "delete_forward_wrap");
      await key("Tab", "Tab", 9);
      await wait(`document.activeElement === ${buttonText(labels.common.delete, dialog)}`, "delete_forward_cycle");
      await key("Tab", "Tab", 9, 8);
      await wait(`document.activeElement === ${buttonText(labels.common.cancel, dialog)}`, "delete_reverse_step");
      await key("Tab", "Tab", 9, 8);
      await wait(`document.activeElement === ${buttonText(labels.common.delete, dialog)}`, "delete_reverse_wrap");
      await key("Escape", "Escape", 27);
      await wait(`!${dialog}`, "delete_cancel");
      if (!viewport.mobile) await wait(`${sidebar}.contains(document.activeElement)`, "delete_focus_restored_to_sidebar");
      await assert("window.__sessionPreview.deletions.length===0 && window.__sessionPreview.removed.length===0", "cancel_has_no_delete_effect");
      await openDelete();
      await click(buttonText(labels.common.delete, dialog));
      await click(buttonText(labels.common.delete, dialog));
      await wait("window.__sessionPreview.deletions.length===1", "rapid_repeat_delete_single_flight");
      await assert(`${buttonText(labels.common.delete, dialog)}.disabled`, "pending_confirm_disabled");
      await key("Escape", "Escape", 27);
      await assert(`Boolean(${dialog})`, "pending_delete_blocks_dismissal");
      await c.evaluate("window.__sessionPreview.resolveDelete(0,false)");
      await wait(`!${dialog}`, "failed_delete_closed");
      await assert("window.__sessionPreview.removed.length===0", "failed_delete_retains_session");
      await screenshot("delete-failure");
      await openDelete();
      await click(buttonText(labels.common.delete, dialog));
      await wait("window.__sessionPreview.deletions.length===2", "delete_retry");
      await c.evaluate("window.__sessionPreview.resolveDelete(1,true)");
      await wait(`!${dialog} && window.__sessionPreview.removed.length===1`, "delete_success");
      await assert("window.__sessionPreview.removed[0]==='a' && window.__sessionPreview.deletions.length===2", "only_target_deleted_once");
      await screenshot("delete-success");
      await noErrors();
      results.push({ theme, viewport, screenshots, measurements, status: "passed" });
    } catch (error) {
      if (browser) measurements.failureUi = await browser.client.evaluate(`(() => {
        const describe = element => element ? { tag: element.tagName, role: element.getAttribute('role'), text: element.textContent.slice(0, 120), title: element.getAttribute('title'), connected: element.isConnected } : null;
        return { active: describe(document.activeElement), deleteAnchor: describe(window.__sessionPreview?.deleteAnchor), deleteMenuItem: describe(window.__sessionPreview?.deleteMenuItem), viewport: { width: innerWidth, height: innerHeight }, dialogs: [...document.querySelectorAll('[role=dialog]')].map(dialog => ({
          text: dialog.textContent.slice(0, 160), animations: dialog.getAnimations({subtree:true}).map(a => ({state:a.playState, time:a.currentTime, iterations:a.effect?.getTiming().iterations, target:describe(a.effect?.target), targetClass:a.effect?.target?.getAttribute('class')})),
          buttons: [...dialog.querySelectorAll('button')].map(button => { const r=button.getBoundingClientRect(); const hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2); return { ...describe(button), rect:r.toJSON(), withinHeight:r.top>=0 && r.bottom<=innerHeight, hit:describe(hit), ownsHit:button.contains(hit), disabled:button.disabled }; })
        })) };
      })()`).catch(diagnosticError => ({ error: String(diagnosticError) }));
      if (browser) screenshots.push(await captureScreenshot(browser.client, evidenceDir, `${prefix}-failed`).catch(() => "capture_failed"));
      failures.push({ theme, viewport, error: String(error), screenshots, measurements });
    } finally { if (browser) await browser.close(); }
  }
} catch (error) {
  failures.push({ stage: "server_or_cleanup", error: String(error) });
} finally {
  if (server) await server.close();
  writeFileSync(resolve(evidenceDir, "browser.log"), log.join("\n"));
  writeFileSync(resolve(evidenceDir, "evidence.json"), JSON.stringify({ schema: "ai-platform.session-preview-browser-smoke.v1", commit, dirty, sourceHashes, mock_backed: true, status: failures.length ? "failed" : "passed", results, failures }, null, 2));
}
console.log(JSON.stringify({ commit, results, failures }));
if (failures.length) process.exitCode = 1;
