import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("RoleSelector uses a native button and checkbox-dialog interaction model", () => {
  const source = readFileSync(
    join(import.meta.dirname, "..", "RoleSelector.tsx"),
    "utf8",
  );

  assert.match(source, /<button[\s\S]*?data-mcp-role-selector-trigger/);
  assert.doesNotMatch(source, /role="combobox"/);
  assert.match(source, /aria-expanded=\{isOpen\}/);
  assert.match(source, /aria-controls="mcp-role-options"/);
  assert.match(source, /aria-haspopup="dialog"/);
  assert.match(source, /data-mcp-role-chip-remove/);
  assert.match(
    source,
    /data-mcp-role-chip-remove[\s\S]*?aria-label=\{t\("mcp\.form\.removeRole", \{ role: name \}\)\}/,
  );
  assert.doesNotMatch(source, /event\.key === " "/);
  assert.match(source, /event\.key === "ArrowDown"/);
  assert.match(source, /setIsOpen\(true\)/);
  assert.match(source, /event\.key === "Escape"/);
  assert.match(source, /closeDropdown\(\)/);
  assert.match(source, /const handleDropdownKeyDown/);
  assert.match(source, /onKeyDown=\{handleDropdownKeyDown\}/);
  assert.match(source, /role="dialog"/);
  assert.match(source, /aria-modal="false"/);
  assert.match(source, /<fieldset/);
  assert.match(source, /type="checkbox"/);
  assert.doesNotMatch(source, /role="listbox"/);
  assert.doesNotMatch(source, /role="option"/);
  assert.match(source, /triggerRef\.current\?\.focus\(\)/);

  const chipRemove = source.indexOf("data-mcp-role-chip-remove");
  const triggerStart = source.indexOf("data-mcp-role-selector-trigger");
  const triggerEnd = source.indexOf("</button>", triggerStart);
  assert.ok(chipRemove >= 0 && triggerStart >= 0 && triggerEnd > triggerStart);
  assert.ok(
    chipRemove < triggerStart || chipRemove > triggerEnd,
    "role chip removal must not be nested inside the selector trigger",
  );
});
