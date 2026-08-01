import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();
const source = (relativePath: string) =>
  readFileSync(join(root, relativePath), "utf8");

test("landing stylesheet residue is retired from the active frontend", () => {
  const main = source("src/main.tsx");
  const sharedPage = source("src/components/share/SharedPage.tsx");
  const scrollButtons = source("src/components/share/ScrollButtons.tsx");
  const componentsCss = source("src/styles/components.css");
  const authSources = [
    "src/components/auth/RegistrationPending.tsx",
    "src/components/auth/ResetPassword.tsx",
    "src/components/auth/VerifyEmail.tsx",
  ]
    .map(source)
    .join("\n");

  assert.equal(existsSync(join(root, "src/styles/landing.css")), false);
  assert.doesNotMatch(main, /styles\/landing\.css/);
  assert.match(sharedPage, /className="share-scroll-progress"/);
  assert.match(componentsCss, /\.share-scroll-progress\s*\{/);
  assert.match(
    componentsCss,
    /animation: share-scroll-progress-shimmer 8s ease infinite/,
  );
  assert.match(componentsCss, /@keyframes share-scroll-progress-shimmer/);
  assert.match(
    componentsCss,
    /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.share-scroll-progress[\s\S]*animation: none !important/,
  );
  assert.doesNotMatch(
    `${sharedPage}\n${scrollButtons}`,
    /landing-(?:progress-bar|scroll-btn)/,
  );
  assert.doesNotMatch(authSources, /blog-btn-(?:primary|ghost)/);
});

test("retired top-level landing translations are absent in every locale", () => {
  for (const locale of ["en", "zh", "ja", "ko", "ru"]) {
    const messages = JSON.parse(
      source(`src/i18n/locales/${locale}.json`),
    ) as Record<string, unknown> & {
      seo?: { landing?: { title?: unknown; description?: unknown } };
    };

    assert.equal(Object.hasOwn(messages, "landing"), false, locale);
    assert.equal(typeof messages.seo?.landing?.title, "string", locale);
    assert.equal(typeof messages.seo?.landing?.description, "string", locale);
  }
});

test("best-practice public assets retain only the manifest icon", () => {
  const assets = readdirSync(join(root, "public/images/best-practice")).sort();
  const manifest = source("public/manifest.json");

  assert.deepEqual(assets, ["mobile-view.webp"]);
  assert.match(manifest, /\/images\/best-practice\/mobile-view\.webp/);
});
