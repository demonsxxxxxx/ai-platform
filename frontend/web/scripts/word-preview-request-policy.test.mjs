import assert from 'node:assert/strict';
import test from 'node:test';
import { isWordFixtureAsset } from './word-preview-request-policy.mjs';
const origins = ['http://127.0.0.1:1234', 'http://localhost:1234'];
test('fixture policy allows only bounded documents and Vite scripts/styles', () => {
  for (const origin of origins) {
    assert.equal(isWordFixtureAsset(`${origin}/scripts/fixtures/word-preview/index.html`, 'document', origins), true);
    assert.equal(isWordFixtureAsset(`${origin}/node_modules/.vite/deps/docx-preview.js?v=1`, 'script', origins), true);
    assert.equal(isWordFixtureAsset(`${origin}/src/styles/tailwind.css`, 'stylesheet', origins), true);
  }
});
test('same-origin, external, nested and type-confused untrusted URLs fail closed', () => {
  for (const path of ['/__word_probe__/document', '/__word_probe__/control/child', '/api/export', '/src/private.png']) {
    for (const type of ['image', 'fetch', 'font', 'document']) {
      assert.equal(isWordFixtureAsset(`${origins[0]}${path}`, type, origins), false);
    }
  }
  assert.equal(isWordFixtureAsset('https://synthetic.invalid/src/module.js', 'script', origins), false);
  assert.equal(isWordFixtureAsset(`${origins[0]}/__word_probe__/document`, 'script', origins), false);
  assert.equal(isWordFixtureAsset(`${origins[0]}/@fs/private`, 'script', origins), false);
});

test('test-only conversion gate preserves real result and rejection through settlement', async () => {
  const { observeWordConversion } = await import('./fixtures/word-preview/conversion-observer.mjs');
  const previous = globalThis.window;
  globalThis.window = {};
  try {
    const result = { kind: 'docx-preview', html: 'synthetic result' };
    assert.equal(await observeWordConversion(async () => result, {}), result);
    for (const fails of [false, true]) {
      const gate = { armed: true }; globalThis.window.__wordSmokeGate = gate;
      const expected = new Error('synthetic conversion failure');
      let published = false;
      const pending = observeWordConversion(async () => { if (fails) throw expected; return result; }, {})
        .then(value => { published = true; return value; }, error => { published = true; return error; });
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(gate.requested, true); assert.equal(gate.settled, true);
      assert.equal(published, false); assert.equal(gate.returned, undefined);
      gate.release();
      assert.equal(await pending, fails ? expected : result);
      assert.equal(gate.returned, true);
    }
  } finally { globalThis.window = previous; }
});
