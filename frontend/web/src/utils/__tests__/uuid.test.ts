import assert from "node:assert/strict";
import test from "node:test";

import { uuid } from "../uuid.ts";

function replaceCrypto(value: Crypto | undefined): () => void {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value,
  });
  return () => {
    if (descriptor) Object.defineProperty(globalThis, "crypto", descriptor);
    else delete (globalThis as { crypto?: Crypto }).crypto;
  };
}

test("fails closed when Web Crypto cannot supply collision-resistant entropy", () => {
  const restore = replaceCrypto(undefined);
  try {
    assert.throws(() => uuid(), /secure_uuid_unavailable/);
  } finally {
    restore();
  }
});

test("uses getRandomValues without Math.random when randomUUID is unavailable", () => {
  let generation = 0;
  const restoreCrypto = replaceCrypto({
    getRandomValues: <T extends ArrayBufferView | null>(array: T): T => {
      generation += 1;
      const bytes = new Uint8Array(
        array!.buffer,
        array!.byteOffset,
        array!.byteLength,
      );
      bytes.fill(generation);
      return array;
    },
  } as Crypto);
  const originalRandom = Math.random;
  Math.random = () => {
    throw new Error("Math.random must not generate authority identities");
  };
  try {
    const first = uuid();
    const second = uuid();
    const uuidV4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
    assert.match(first, uuidV4);
    assert.match(second, uuidV4);
    assert.notEqual(first, second);
  } finally {
    Math.random = originalRandom;
    restoreCrypto();
  }
});
