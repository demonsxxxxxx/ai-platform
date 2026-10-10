import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { Script } from "node:vm";
import ts from "typescript";
import { isPwaSkipWaitingMessage } from "../pwaGuards";
import { getPwaRequestKind } from "../pwaRouting";

interface RouteOptions {
  request: { method: string; mode: string; url: string; headers: Headers };
}

test("SW navigation uses the installed Workbox precache revision for offline fallbacks", async (t) => {
  const location = new URL("https://app.example.test/sw.js");
  const registration = { scope: "https://app.example.test/" };
  const stores = new Map<string, Map<string, Response>>();
  const cacheLookups: Array<{ cacheName: string; key: string }> = [];
  const keyFor = (request: Request | string) => new URL(
    typeof request === "string" ? request : request.url, location,
  ).href;
  const caches = {
    async open(cacheName: string) {
      return {
        async match(request: Request | string) {
          const key = keyFor(request);
          cacheLookups.push({ cacheName, key });
          return stores.get(cacheName)?.get(key)?.clone();
        },
      };
    },
    async match(request: Request | string) {
      for (const cacheName of stores.keys()) {
        const response = await (await this.open(cacheName)).match(request);
        if (response) return response;
      }
      return undefined;
    },
  };
  const worker = {
    location,
    registration,
    caches,
    __WB_MANIFEST: [
      { url: "offline.html", revision: "current-offline" },
      { url: "index.html", revision: "current-index" },
    ],
    addEventListener() {},
  };
  const globals = { self: worker, location, registration, caches };
  const previous = new Map(Object.keys(globals).map((key) => [
    key, Object.getOwnPropertyDescriptor(globalThis, key),
  ]));
  for (const [key, value] of Object.entries(globals)) {
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  }

  try {
    // Keep Workbox's real precache registration, cache naming and revision lookup.
    // Only browser storage and the navigation network result are controlled here.
    const [precaching, core, cacheable, expiration, strategies] = await Promise.all([
      import("workbox-precaching"),
      import("workbox-core"),
      import("workbox-cacheable-response"),
      import("workbox-expiration"),
      import("workbox-strategies"),
    ]);
    let networkFails = true;
    let navigationResponse: Response | undefined;
    const routes: Array<{
      match: (options: RouteOptions) => boolean;
      handler: unknown;
    }> = [];
    const modules: Record<string, unknown> = {
      "workbox-precaching": precaching,
      "workbox-core": core,
      "workbox-cacheable-response": cacheable,
      "workbox-expiration": expiration,
      "workbox-strategies": {
        ...strategies,
        NetworkFirst: class {
          async handle() {
            if (networkFails) throw new TypeError("offline");
            return navigationResponse;
          }
        },
      },
      "workbox-routing": {
        registerRoute(match: (options: RouteOptions) => boolean, handler: unknown) {
          routes.push({ match, handler });
        },
      },
      "./pwaGuards": { isPwaSkipWaitingMessage },
      "./pwaRouting": { getPwaRequestKind },
    };
    const source = readFileSync(new URL("../sw.ts", import.meta.url), "utf8");
    const compiled = ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    }).outputText;
    new Script(compiled, { filename: "sw.ts" }).runInNewContext({
      ...globals,
      Response,
      exports: {},
      require(name: string) {
        assert.ok(name in modules, `Unexpected SW dependency: ${name}`);
        return modules[name];
      },
    });
    const navigation = routes[0];
    assert.ok(navigation);
    assert.equal(typeof navigation.handler, "function");
    const handle = navigation.handler as (options: RouteOptions) => Promise<Response>;
    const options: RouteOptions = {
      request: { method: "GET", mode: "navigate", url: `${location.origin}/chat/new`, headers: new Headers() },
    };
    assert.equal(navigation.match(options), true);

    const offlineKey = precaching.getCacheKeyForURL("/offline.html");
    const indexKey = precaching.getCacheKeyForURL("/index.html");
    assert.ok(offlineKey);
    assert.ok(indexKey);
    assert.match(offlineKey, /__WB_REVISION__=current-offline/);
    assert.match(indexKey, /__WB_REVISION__=current-index/);
    const precache = new Map<string, Response>();
    stores.set(core.cacheNames.precache, precache);
    const html = (body: string) => new Response(body, { headers: { "Content-Type": "text/html" } });

    await t.test("failed navigation prefers the revisioned offline page", async () => {
      precache.set(offlineKey, html("offline page"));
      precache.set(indexKey, html("app shell"));
      const response = await handle(options);
      assert.equal(response.status, 200);
      assert.equal(await response.text(), "offline page");
      assert.deepEqual(cacheLookups, [{ cacheName: core.cacheNames.precache, key: offlineKey }]);
    });

    await t.test("missing offline page falls back to the revisioned app shell", async () => {
      precache.delete(offlineKey);
      const response = await handle(options);
      assert.equal(response.status, 200);
      assert.equal(await response.text(), "app shell");
    });

    await t.test("stale revisions and unrelated caches cannot satisfy the fallback", async () => {
      precache.clear();
      precache.set(offlineKey.replace("current-offline", "stale-offline"), html("stale revision"));
      stores.set("unrelated-cache", new Map([[keyFor("/offline.html"), html("unrelated response")]]));
      const response = await handle(options);
      assert.equal(response.status, 503);
      assert.equal(response.headers.get("Content-Type"), "text/plain; charset=utf-8");
      assert.equal(await response.text(), "AI Platform is offline.");
    });

    await t.test("an empty navigation response also uses the current offline page", async () => {
      networkFails = false;
      precache.set(offlineKey, html("offline page"));
      assert.equal(await (await handle(options)).text(), "offline page");
    });

    await t.test("successful navigation does not consult fallback caches", async () => {
      navigationResponse = html("fresh navigation");
      cacheLookups.length = 0;
      assert.equal(await (await handle(options)).text(), "fresh navigation");
      assert.deepEqual(cacheLookups, []);
    });
  } finally {
    for (const [key, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    }
  }
});
