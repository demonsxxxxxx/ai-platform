import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import postcss from "postcss";

// jsdom is the pinned mounted-test runtime and does not ship declarations here.
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import { PanelHeader } from "../../../components/common/PanelHeader";

const wordReviewCss = readFileSync(
  new URL("../wordReviewApplication.css", import.meta.url), "utf8",
);
const sharedCss = readFileSync(
  new URL("../../../styles/components.css", import.meta.url), "utf8",
);

test("loading and leaving Word Review preserves unrelated headers and generic controls", () => {
  const header = renderToStaticMarkup(createElement(PanelHeader, {
    title: "MCP",
    subtitle: "Tool catalog",
    searchValue: "",
    onSearchChange: () => {},
    searchPlaceholder: "Search",
  }));
  const controls = `
    <button class="primary-button">Primary</button>
    <button class="secondary-button">Secondary</button>
    <button class="icon-button">Icon</button>
    <div class="empty-state">Empty</div>
    <div class="status-item"><span class="status-value">1</span></div>
    <div class="file-icon">File</div>
    <div class="task-name">Task</div>
    <div class="progress-track"><span>Progress</span></div>`;
  const dom = new JSDOM(`<!doctype html><html><head></head><body>
    <section id="other-route">${header}${controls}</section>
    <main class="review-page"><div class="panel-header"><h2>Review</h2></div>${controls}</main>
    </body></html>`);
  const { document } = dom.window;
  const addStyles = (css: string) => {
    const style = document.createElement("style");
    style.textContent = css;
    document.head.append(style);
  };
  const properties = ["display", "color", "backgroundColor", "padding", "margin", "fontSize", "borderRadius"] as const;
  const snapshotOtherRoute = () => Array.from(
    document.querySelectorAll("#other-route *"),
    (element: Element) => {
      const style = dom.window.getComputedStyle(element);
      return Object.fromEntries(properties.map((property) => [property, style[property]]));
    },
  );

  try {
    addStyles(sharedCss);
    const before = snapshotOtherRoute();
    addStyles(wordReviewCss);
    assert.deepEqual(snapshotOtherRoute(), before, "route CSS must not change unrelated elements");

    // The owning application keeps the styles that leaked to the other route.
    assert.equal(dom.window.getComputedStyle(document.querySelector(".review-page .panel-header")).display, "flex");
    assert.equal(dom.window.getComputedStyle(document.querySelector(".review-page .primary-button")).backgroundColor, "rgb(79, 70, 229)");
    assert.equal(dom.window.getComputedStyle(document.querySelector(".review-page .empty-state")).display, "flex");

    document.querySelector(".review-page").remove();
    assert.deepEqual(snapshotOtherRoute(), before, "retained CSS must remain harmless after route navigation");
  } finally {
    dom.window.close();
  }
});

test("all Word Review selectors, including responsive rules, stay within review-page", () => {
  const stylesheet = postcss.parse(wordReviewCss);
  let selectorsChecked = 0;
  stylesheet.walkRules((rule) => {
    for (const selector of rule.selectors) {
      assert.match(selector, /^\.review-page(?:$|\s)/, `Unscoped selector: ${selector}`);
      selectorsChecked += 1;
    }
  });
  assert.ok(selectorsChecked > 0);
});
