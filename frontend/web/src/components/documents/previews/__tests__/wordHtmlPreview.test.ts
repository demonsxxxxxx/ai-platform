import assert from "node:assert/strict";
import test from "node:test";
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";

test("Word frame removes executable/navigation markup and preserves styles, fonts and merges", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>");
  Object.assign(globalThis, { window: dom.window, document: dom.window.document });
  const { prepareWordPreviewDocument } = await import("../wordHtmlPreview.ts");
  const result = prepareWordPreviewDocument({ html: `<style>.docx-wrapper{display:flex}</style><div><style>@font-face{font-family:Embedded;src:url(data:font/woff;base64,AAAA)}.docx{width:612pt}</style><section class="docx"><table><tr><td colspan="2" rowspan="3">cell</td></tr></table><script>parent.compromised=true</script><meta http-equiv="refresh" content="0;url=https://synthetic.invalid"><a href="https://synthetic.invalid/link">external</a><a href="#page2">internal</a><a href="javascript:alert(1)">bad</a><img src="data:image/png;base64,AAAA" onerror="alert(1)"><img src="https://synthetic.invalid/img"><svg style="background:url(https://synthetic.invalid/css)"></svg></section></div>` });
  const parsed = new dom.window.DOMParser().parseFromString(result, "text/html");
  assert.equal(parsed.querySelector("script,iframe,meta[http-equiv=refresh]"), null);
  assert.equal(parsed.querySelector("td")?.getAttribute("colspan"), "2");
  assert.equal(parsed.querySelector("td")?.getAttribute("rowspan"), "3");
  assert.deepEqual(Array.from(parsed.querySelectorAll("a[href]"), (a: unknown) => (a as Element).getAttribute("href")), ["#page2"]);
  assert.equal(parsed.querySelector("img")?.getAttribute("src"), "data:image/png;base64,AAAA");
  assert.equal(parsed.querySelector("[onerror]"), null);
  assert.match(result, /@font-face/);
  assert.match(result, /\.docx-wrapper\{display:flex\}/);
  assert.match(result, /width:612pt/);
  assert.equal(parsed.head.firstElementChild?.getAttribute("http-equiv"), "Content-Security-Policy");
  assert.match(parsed.head.firstElementChild?.getAttribute("content") ?? "", /default-src 'none'.*font-src data: blob:/);
  assert.match(result, /https:\/\/synthetic.invalid\/css/);
  const darkFallback = prepareWordPreviewDocument({ html: "<p>Fallback</p>", className: "word-preview-content dark", dark: true });
  assert.match(darkFallback, /background:#111827/);
  assert.match(darkFallback, /class="word-preview-content dark"/);
  assert.match(result, /background:white/);
  assert.equal(document.body.innerHTML, "");
  dom.window.close();
});
