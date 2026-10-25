#!/usr/bin/env node
// Enforces docs/frontend/lobehub-visual-guidelines.md section 12.
// Zero-tolerance rules fail on any new violation. Ratchet rules compare each
// file against scripts/style-rules-baseline.json and fail only when a count grows.
import { readFileSync, writeFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join, relative, extname, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "src");
const baselinePath = join(root, "scripts", "style-rules-baseline.json");
const update = process.argv.includes("--update-baseline");

// Intentional rendering palettes: document previews, code/diagram canvases, data charts, brand logos.
const RENDER_EXCEPTIONS = [
  /components\/documents\/previews\//,
  /components\/common\/CodeMirrorViewer\.tsx$/,
  /components\/chat\/ChatMessage\/MermaidDiagram\.tsx$/,
  /features\/usage\/UsagePage\.tsx$/,
  /components\/auth\/AuthPage\.tsx$/,
  /styles\/syntax-highlight\.css$/,
  /styles\/excel-preview\.css$/,
  /components\/documents\/previews\/.*\.css$/,
];
// Shadow colors follow the black-based shadow convention; the browser theme-color meta needs literal values.
const SHADOW_LINE = /shadow|boxShadow/;
const LITERAL_EXCEPTIONS = [/utils\/themeDom\.ts$/];
const ALLOWED_BREAKPOINTS = new Set([639, 640, 767, 768, 1023, 1024, 1279, 1280, 1535, 1536]);
const RAW_PALETTE = /(?<![\w-])(?:bg|text|border|ring|from|to|via|fill|stroke|divide|placeholder|outline|decoration|accent|shadow)-(?:stone|slate|red|amber|blue|emerald|green|purple|cyan|orange|teal|sky|violet|rose|indigo|yellow|pink|fuchsia|lime|zinc|gray|white|black)(?:-\d{2,3})?(?![\w-])/g;
const HEX6 = /#[0-9a-fA-F]{6}\b/g;
const HEX3 = /#(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{3}\b/g;
const RGBA = /rgba?\(\s*\d/g;

const walk = (dir, out = []) => {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
};
const rel = (p) => relative(root, p).split("\\").join("/");
const isTest = (p) => /__tests__\/|\.test\.(tsx?|mjs)$/.test(p);
const isRenderException = (p) => RENDER_EXCEPTIONS.some((re) => re.test(p));

const failures = [];
const counts = { inlineStyle: {}, important: {} };

for (const file of walk(src)) {
  // Match on the POSIX-relative path so exception patterns hold on Windows runners.
  const r = "src/" + rel(file).slice("src/".length);
  const ext = extname(file);
  if (![".tsx", ".ts", ".css"].includes(ext) || isTest(r)) continue;
  const text = readFileSync(file, "utf8");
  const lines = text.split("\n");

  if (ext === ".css") {
    const important = (text.match(/!important/g) || []).length;
    if (important) counts.important[r] = important;
    if (isRenderException(r)) continue;
    let inShadow = false;
    lines.forEach((line, i) => {
      if (/^\s*--theme-/.test(line)) return;
      if (/^\s*(box-shadow|text-shadow|filter)\s*:|shadow[\w-]*\s*:/.test(line)) inShadow = true;
      const literal = /#[0-9a-fA-F]{3,6}\b|rgba?\(\s*\d/.test(line);
      if (literal && !inShadow) failures.push(`${r}:${i + 1} literal color in CSS (use --theme-* tokens)`);
      if (/;\s*$|}\s*$/.test(line)) inShadow = false;
      for (const m of line.matchAll(/@media[^{]*/g)) {
        for (const n of m[0].matchAll(/(\d{3,4})px/g)) {
          if (!ALLOWED_BREAKPOINTS.has(Number(n[1]))) failures.push(`${r}:${i + 1} breakpoint ${n[1]}px is not in the allowed set`);
        }
      }
    });
    continue;
  }

  // TSX / TS
  const inline = (text.match(/style=\{\{/g) || []).length;
  if (inline) counts.inlineStyle[r] = inline;
  if (isRenderException(r)) continue;
  lines.forEach((line, i) => {
    const code = line.replace(/\/\/.*$/, "");
    if (/^\s*(\/\/|\*|\/\*)/.test(line)) return;
    const literalAllowed = SHADOW_LINE.test(code) || LITERAL_EXCEPTIONS.some((re) => re.test(r));
    for (const m of code.matchAll(RAW_PALETTE)) failures.push(`${r}:${i + 1} raw palette class "${m[0]}" (use semantic token)`);
    if (!literalAllowed && (HEX6.test(code) || HEX3.test(code) || RGBA.test(code))) {
      HEX6.lastIndex = 0; HEX3.lastIndex = 0; RGBA.lastIndex = 0;
      failures.push(`${r}:${i + 1} literal color in source (use --theme-* tokens)`);
    }
    HEX6.lastIndex = 0; HEX3.lastIndex = 0; RGBA.lastIndex = 0;
  });
}

const current = { counts };
if (update) {
  writeFileSync(baselinePath, JSON.stringify(current, null, 2) + "\n");
  console.log(`baseline written: ${Object.keys(counts.inlineStyle).length} files with inline style, ${Object.keys(counts.important).length} CSS files with !important`);
  process.exit(failures.length ? 1 : 0);
}

const baseline = existsSync(baselinePath) ? JSON.parse(readFileSync(baselinePath, "utf8")) : { counts: { inlineStyle: {}, important: {} } };
for (const kind of ["inlineStyle", "important"]) {
  for (const [file, n] of Object.entries(counts[kind])) {
    const allowed = baseline.counts[kind][file] ?? 0;
    if (n > allowed) failures.push(`${file} ${kind === "inlineStyle" ? "inline style={{}}" : "!important"} count ${n} exceeds baseline ${allowed}`);
  }
}

if (failures.length) {
  console.error(`style rules: ${failures.length} problem(s)`);
  for (const f of failures.slice(0, 200)) console.error("  " + f);
  process.exit(1);
}
const totalInline = Object.values(counts.inlineStyle).reduce((a, b) => a + b, 0);
const totalImportant = Object.values(counts.important).reduce((a, b) => a + b, 0);
console.log(`style rules: ok (inline style debt ${totalInline}, !important debt ${totalImportant}; ratcheted)`);
