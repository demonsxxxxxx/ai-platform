import fs from "node:fs";
import type { IncomingMessage } from "node:http";
import path from "node:path";
import { Buffer } from "node:buffer";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import {
  LOCAL_AUTH_PROXY_STRIPPED_HEADERS,
  localDevAuthBootstrapResponse,
  resolveLocalDevAuthProxy,
  type LocalDevAuthProxyConfig,
} from "./dev-auth-proxy";

const ICONS_DIR = path.resolve(__dirname, "public/icons");
const AI_PLATFORM_API_TARGET =
  process.env.VITE_AI_PLATFORM_API_TARGET || "http://127.0.0.1:8020";

function getStaticIconContentType(filePath: string): string {
  if (filePath.endsWith(".svg")) return "image/svg+xml";
  if (filePath.endsWith(".png")) return "image/png";
  if (filePath.endsWith(".jpg") || filePath.endsWith(".jpeg")) {
    return "image/jpeg";
  }
  if (filePath.endsWith(".webp")) return "image/webp";
  if (filePath.endsWith(".ico")) return "image/x-icon";
  return "application/octet-stream";
}

const cacheStableIconsPlugin = {
  name: "cache-stable-icons",
  configureServer(server: {
    middlewares: {
      use: (
        handler: (
          req: { method?: string; url?: string },
          res: {
            statusCode?: number;
            setHeader: (name: string, value: string) => void;
            end: (body: Buffer) => void;
          },
          next: () => void,
        ) => void,
      ) => void;
    };
  }) {
    server.middlewares.use((req, res, next) => {
      if (req.method !== "GET" && req.method !== "HEAD") {
        next();
        return;
      }

      const requestPath = req.url?.split("?")[0];
      if (!requestPath?.startsWith("/icons/")) {
        next();
        return;
      }

      const relativePath = requestPath.slice("/icons/".length);
      if (
        !relativePath ||
        relativePath.includes("..") ||
        relativePath.includes("\\")
      ) {
        next();
        return;
      }

      const filePath = path.join(ICONS_DIR, relativePath);
      if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
        next();
        return;
      }

      const fileBuffer = fs.readFileSync(filePath);
      res.statusCode = 200;
      res.setHeader("Content-Type", getStaticIconContentType(filePath));
      res.setHeader("Content-Length", String(fileBuffer.length));
      res.setHeader("Cache-Control", "public, max-age=31536000, immutable");
      if (req.method === "HEAD") {
        res.end(Buffer.alloc(0));
        return;
      }
      res.end(fileBuffer);
    });
  },
};

const LOCAL_AUTH_BOOTSTRAP_PATH = "/api/ai/auth/bootstrap";
const LOCAL_AUTH_BOOTSTRAP_MAX_BYTES = 4096;

async function readBoundedJson(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += bytes.byteLength;
    if (size > LOCAL_AUTH_BOOTSTRAP_MAX_BYTES) {
      throw new Error("local_auth_bootstrap_payload_too_large");
    }
    chunks.push(bytes);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function localDevAuthBootstrapPlugin(
  config: LocalDevAuthProxyConfig | null,
): Plugin {
  return {
    name: "local-dev-auth-bootstrap",
    configureServer(server) {
      if (!config) return;
      server.middlewares.use(async (request, response, next) => {
        if (
          request.method !== "POST" ||
          request.url?.split("?", 1)[0] !== LOCAL_AUTH_BOOTSTRAP_PATH
        ) {
          next();
          return;
        }
        try {
          const payload = await readBoundedJson(request);
          const projection = localDevAuthBootstrapResponse(payload);
          if (!projection) {
            response.statusCode = 400;
            response.setHeader("Content-Type", "application/json");
            response.end(JSON.stringify({ detail: "invalid_local_auth_bootstrap" }));
            return;
          }
          response.statusCode = 200;
          response.setHeader("Content-Type", "application/json");
          response.setHeader("Cache-Control", "no-store");
          response.end(JSON.stringify(projection));
        } catch {
          response.statusCode = 400;
          response.setHeader("Content-Type", "application/json");
          response.end(JSON.stringify({ detail: "invalid_local_auth_bootstrap" }));
        }
      });
    },
  };
}

export default defineConfig(({ command }) => {
  const localDevAuthProxy = resolveLocalDevAuthProxy({
    apiTarget: AI_PLATFORM_API_TARGET,
    command,
    env: process.env,
  });

  return {
    plugins: [
      react(),
      VitePWA({
        strategies: "injectManifest",
        srcDir: "src",
        filename: "sw.ts",
        injectRegister: false,
        manifest: false,
        injectManifest: {
          globPatterns: [
            "**/*.{js,css,html,ico,png,svg,webp,avif,woff,woff2,json}",
          ],
          maximumFileSizeToCacheInBytes: 8 * 1024 * 1024,
        },
        includeManifestIcons: false,
        devOptions: {
          enabled: false,
        },
      }),
      cacheStableIconsPlugin,
      localDevAuthBootstrapPlugin(localDevAuthProxy),
    ],
    resolve: {
      alias: [
        {
          find: /^opentype\.js$/,
          replacement: path.resolve(
            __dirname,
            "node_modules/opentype.js/dist/opentype.js",
          ),
        },
        {
          find: /^stream$/,
          replacement: path.resolve(__dirname, "node_modules/stream-browserify"),
        },
        {
          find: /^events$/,
          replacement: path.resolve(__dirname, "node_modules/events"),
        },
        {
          find: /^util$/,
          replacement: path.resolve(__dirname, "node_modules/util"),
        },
        {
          find: /^process$/,
          replacement: path.resolve(__dirname, "node_modules/process/browser"),
        },
      ],
    },
    esbuild: {
      drop: process.env.NODE_ENV === "production" ? ["console", "debugger"] : [],
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            "vendor-react": ["react", "react-dom", "react-router-dom"],
            "vendor-codemirror": [
              "@uiw/react-codemirror",
              "@codemirror/lang-css",
              "@codemirror/lang-html",
              "@codemirror/lang-javascript",
              "@codemirror/lang-json",
              "@codemirror/lang-markdown",
              "@codemirror/lang-python",
              "@codemirror/lang-sql",
              "@codemirror/lang-yaml",
            ],
            "vendor-markdown": [
              "react-markdown",
              "remark-gfm",
              "remark-breaks",
              "remark-math",
              "rehype-katex",
              "rehype-highlight",
            ],
            "vendor-sandpack": ["@codesandbox/sandpack-react"],
            "vendor-mermaid": ["mermaid"],
            "vendor-katex": ["katex"],
            "vendor-i18n": ["i18next", "react-i18next"],
          },
        },
      },
    },
    server: {
      // A dev-auth proxy carries a synthetic principal, so never expose that
      // server beyond the local machine. Normal development keeps the old host.
      host: localDevAuthProxy?.serverHost ?? true,
      port: 3001,
      proxy: {
        "/api": {
          target: AI_PLATFORM_API_TARGET,
          changeOrigin: true,
          secure: false,
          ws: true,
          timeout: 300000,
          proxyTimeout: 300000,
          configure: (proxy) => {
            proxy.on("proxyReq", (proxyReq, req) => {
              const host = req.headers.host;
              if (host) {
                proxyReq.setHeader("X-Forwarded-Host", host);
              }
              if (localDevAuthProxy) {
                for (const name of LOCAL_AUTH_PROXY_STRIPPED_HEADERS) {
                  proxyReq.removeHeader(name);
                }
              }
              for (const [name, value] of Object.entries(
                localDevAuthProxy?.headers ?? {},
              )) {
                proxyReq.setHeader(name, value);
              }
            });
          },
        },
      },
    },
  };
});
