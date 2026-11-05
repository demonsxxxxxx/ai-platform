// Real pointer-event regression for externally anchored model/settings menus.
import assert from "node:assert/strict";
import { startBrowser, sleep } from "./browser-smoke-harness.mjs";

const baseUrl = (process.env.AI_PLATFORM_FRONTEND_URL || "http://127.0.0.1:5194").replace(/\/+$/, "");
for (const width of [1440, 390]) {
  const browser = await startBrowser({ viewport: { width, height: 844, mobile: width === 390 } });
  const { client } = browser;
  const click = async (selector) => {
    const point = await client.evaluate(`(() => {
      const node = [...document.querySelectorAll(${JSON.stringify(selector)})].find((item) => {
        const rect = item.getBoundingClientRect();
        return item.checkVisibility() && rect.left >= 0 && rect.right <= innerWidth;
      });
      if (!node) throw new Error('trigger_not_visible');
      const rect = node.getBoundingClientRect();
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    })()`);
    for (const type of ["mousePressed", "mouseReleased"]) {
      await client.send("Input.dispatchMouseEvent", { type, button: "left", clickCount: 1, ...point });
    }
    await sleep(250);
  };
  try {
    await client.send("Page.navigate", { url: `${baseUrl}/scripts/fixtures/generic-chat-preview.html` });
    await client.waitFor("!!document.querySelector('textarea')", "composer_ready");
    assert.ok(await client.evaluate("!!document.querySelector('[data-synthetic-preview-banner]')"));
    const menus = [{ trigger: "[data-workbench-menu-trigger]", popup: ".workbench-menu-popup" }];
    // The mobile model selector is a modal Drawer; its backdrop intentionally owns closing.
    if (width >= 640) menus.push({ trigger: "[data-composer-model-trigger]", popup: "[data-composer-model-panel]" });
    for (const { trigger, popup } of menus) {
      const open = `!!document.querySelector(${JSON.stringify(popup)})`;
      await click(trigger);
      await client.waitFor(open, `${trigger}:open`);
      await click(trigger);
      await client.waitFor(`!(${open})`, `${trigger}:second_click_closes`);
      await click(trigger);
      await client.waitFor(open, `${trigger}:reopen`);
      await client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await client.waitFor(`!(${open})`, `${trigger}:escape`);
      await click(trigger);
      await client.waitFor(open, `${trigger}:open_again`);
      await click("textarea");
      await client.waitFor(`!(${open})`, `${trigger}:outside_click`);
    }
    console.log(`${width}: model/settings pointer toggle, Escape and outside click passed`);
  } finally {
    await browser.close();
  }
}
