import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();
const source = (relativePath: string) =>
  readFileSync(join(root, relativePath), "utf8");

test("landing stylesheet residue is retired from the active frontend", () => {
  const main = source("src/main.tsx");

  assert.equal(existsSync(join(root, "src/styles/landing.css")), false);
  assert.doesNotMatch(main, /styles\/landing\.css/);
});

test("retired landing translations are absent from the Chinese catalog", () => {
  const locale = "zh";
  const messages = JSON.parse(
    source("src/i18n/locales/zh.json"),
  ) as Record<string, unknown> & {
    seo?: Record<string, unknown>;
  };

  assert.equal(Object.hasOwn(messages, "landing"), false, locale);
  assert.equal(Object.hasOwn(messages.seo ?? {}, "landing"), false, locale);
});

test("best-practice public assets retain only the manifest icon", () => {
  const assets = readdirSync(join(root, "public/images/best-practice")).sort();
  const manifest = source("public/manifest.json");

  assert.deepEqual(assets, ["mobile-view.webp"]);
  assert.match(manifest, /\/images\/best-practice\/mobile-view\.webp/);
});
