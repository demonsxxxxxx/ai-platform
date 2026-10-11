import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';

// The pinned upstream SW-block init script reads a forbidden getter in opaque
// frames. These locations follow its exact source + InitScript IIFE, not an
// arbitrary error observed in the application under test.
export const PLAYWRIGHT_SW_ERROR = Object.freeze({
  name: 'SecurityError',
  message: "Failed to read the 'serviceWorker' property from 'Navigator': Service worker is disabled because the context is sandboxed and lacks the 'allow-same-origin' flag.",
});
export const PLAYWRIGHT_SW_STACK = `SecurityError: ${PLAYWRIGHT_SW_ERROR.message}\n    at <anonymous>:3:15\n    at <anonymous>:5:7`;
export function isPinnedPlaywrightSwError(error) {
  return error.name === PLAYWRIGHT_SW_ERROR.name && error.message === PLAYWRIGHT_SW_ERROR.message && error.stack === PLAYWRIGHT_SW_STACK;
}
export function verifyPinnedPlaywrightSwSource() {
  const require = createRequire(import.meta.url);
  const manifestPath = require.resolve('playwright-core/package.json');
  assert.equal(JSON.parse(readFileSync(manifestPath, 'utf8')).version, '1.62.1');
  const source = readFileSync(join(dirname(manifestPath), 'lib/coreBundle.js'), 'utf8');
  assert.ok(source.includes('if (this._options.serviceWorkers === "block")\n          await this.addInitScript(nullProgress, `\nif (navigator.serviceWorker) navigator.serviceWorker.register = async () => { console.warn(\'Service Worker registration blocked by Playwright\'); };\n`);'));
  assert.ok(source.includes('this.source = `(() => {\n      ${source12}\n    })();`;'));
}
