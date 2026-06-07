import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

test("file operation pills render full file paths without path-breaking formatting", () => {
  const readFileItem = readSource("../ReadFileItem.tsx");
  const writeFileItem = readSource("../WriteFileItem.tsx");
  const editFileItem = readSource("../EditFileItem.tsx");
  const lsItem = readSource("../LsItem.tsx");

  assert.match(
    readFileItem,
    /label=\{`\$\{t\("chat\.message\.toolRead"\)\} \$\{filePath \|\| ""\}`\}/,
  );
  assert.match(readFileItem, /formatLabel=\{false\}/);

  assert.match(
    writeFileItem,
    /label=\{`\$\{t\("chat\.message\.toolWrite"\)\} \$\{filePath \|\| ""\}`\}/,
  );
  assert.match(writeFileItem, /formatLabel=\{false\}/);

  assert.match(
    editFileItem,
    /label=\{`\$\{t\("chat\.message\.toolEdit"\)\} \$\{filePath \|\| ""\}`\}/,
  );
  assert.match(editFileItem, /formatLabel=\{false\}/);

  assert.match(
    lsItem,
    /label=\{`\$\{t\("chat\.message\.toolLs"\)\} \$\{dirPath\}`\}/,
  );
  assert.match(lsItem, /formatLabel=\{false\}/);
});

test("collapsible pill always truncates labels to prevent overflow", () => {
  const source = readFileSync(
    new URL("../../../../common/CollapsiblePill.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /"font-mono min-w-0 truncate overflow-hidden"/);
});

test("collapsible pill can preserve labels without path-breaking formatting", () => {
  const source = readFileSync(
    new URL("../../../../common/CollapsiblePill.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /formatLabel\?: boolean/);
  assert.match(
    source,
    /const displayedLabel = formatLabel \? formattedLabel : label/,
  );
  assert.match(source, /\{displayedLabel\}/);
});
