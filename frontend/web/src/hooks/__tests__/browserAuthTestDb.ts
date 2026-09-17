type StoredValue = Record<string, unknown> | undefined;

function clone(value: StoredValue): StoredValue {
  return value === undefined ? undefined : structuredClone(value);
}

/** Install the IndexedDB subset used by the V2 browser-auth coordinator. */
export function installBrowserAuthTestDb(): void {
  let stored: StoredValue;
  let storeExists = false;

  const database = {
    onversionchange: null as (() => void) | null,
    close() {},
    get objectStoreNames() {
      return { contains: () => storeExists };
    },
    createObjectStore() {
      storeExists = true;
      return {};
    },
    transaction() {
      let aborted = false;
      let pending: StoredValue;
      const transaction = {
        onabort: null as (() => void) | null,
        oncomplete: null as (() => void) | null,
        onerror: null as (() => void) | null,
        abort() {
          if (aborted) return;
          aborted = true;
          queueMicrotask(() => transaction.onabort?.());
        },
        objectStore() {
          return {
            get() {
              const request = {
                result: undefined as StoredValue,
                onerror: null as (() => void) | null,
                onsuccess: null as (() => void) | null,
              };
              queueMicrotask(() => {
                if (aborted) return;
                request.result = clone(stored);
                request.onsuccess?.();
                queueMicrotask(() => {
                  if (aborted) return;
                  if (pending !== undefined) stored = clone(pending);
                  transaction.oncomplete?.();
                });
              });
              return request;
            },
            put(value: Record<string, unknown>) {
              pending = clone(value);
              return {};
            },
          };
        },
      };
      return transaction;
    },
  };

  const factory = {
    open() {
      const request = {
        result: database,
        transaction: { abort() {} },
        onblocked: null as (() => void) | null,
        onerror: null as (() => void) | null,
        onsuccess: null as (() => void) | null,
        onupgradeneeded: null as (() => void) | null,
      };
      queueMicrotask(() => {
        if (!storeExists) request.onupgradeneeded?.();
        request.onsuccess?.();
      });
      return request;
    },
  };

  Object.defineProperty(globalThis, "indexedDB", {
    configurable: true,
    value: factory,
  });
}
