export const MCP_GATEWAY_JWT_STORAGE_KEY = "mcp_gateway_jwt";
export const MCP_AUTH_READY_MESSAGE = "ai-platform:mcp-auth-ready";
export const MCP_AUTH_MESSAGE = "ai-platform:mcp-auth";
export const MCP_GATEWAY_AUTH_CHANGED_EVENT = "mcp-gateway-auth-changed";

const MCP_AUTH_NONCE_BYTES = 32;
const MCP_AUTH_HANDOFF_TIMEOUT_MS = 10_000;

function browserLocalStorage(): Storage | null {
  return typeof localStorage === "undefined" ? null : localStorage;
}

function publishMcpGatewayAuthChanged(): void {
  if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
    window.dispatchEvent(new Event(MCP_GATEWAY_AUTH_CHANGED_EVENT));
  }
}

export function getMcpGatewayJwt(): string | null {
  return browserLocalStorage()?.getItem(MCP_GATEWAY_JWT_STORAGE_KEY) ?? null;
}

export function setMcpGatewayJwt(jwt: string): void {
  const normalized = jwt.trim();
  if (!normalized) {
    clearMcpGatewayJwt();
    return;
  }
  browserLocalStorage()?.setItem(MCP_GATEWAY_JWT_STORAGE_KEY, normalized);
  publishMcpGatewayAuthChanged();
}

export function clearMcpGatewayJwt(): void {
  const storage = browserLocalStorage();
  const hadCredential = storage?.getItem(MCP_GATEWAY_JWT_STORAGE_KEY) !== null;
  storage?.removeItem(MCP_GATEWAY_JWT_STORAGE_KEY);
  if (hadCredential) publishMcpGatewayAuthChanged();
}

function createHandoffNonce(): string | null {
  if (!globalThis.crypto?.getRandomValues) return null;
  return Array.from(
    globalThis.crypto.getRandomValues(new Uint8Array(MCP_AUTH_NONCE_BYTES)),
    (value) => value.toString(16).padStart(2, "0"),
  ).join("");
}

export function configuredMcpAuthSourceOrigin(): string | null {
  const configured = import.meta.env?.VITE_MCP_AUTH_SOURCE_ORIGIN?.trim();
  if (!configured) return null;
  try {
    const parsed = new URL(configured);
    if (parsed.origin !== configured || !["http:", "https:"].includes(parsed.protocol)) {
      return null;
    }
    return parsed.origin;
  } catch {
    return null;
  }
}

export function isMcpAuthHandoffMessage(
  value: unknown,
  expectedNonce: string,
): value is { type: typeof MCP_AUTH_MESSAGE; nonce: string; token: string } {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return (
    data.type === MCP_AUTH_MESSAGE &&
    data.nonce === expectedNonce &&
    typeof data.token === "string" &&
    Boolean(data.token.trim())
  );
}

/**
 * Request a one-time MCP JWT handoff from the exact opener and configured
 * source Origin. The JWT is accepted only in the matching nonce response.
 */
export function installMcpAuthHandoff(): () => void {
  const sourceOrigin = configuredMcpAuthSourceOrigin();
  const opener = window.opener;
  const nonce = createHandoffNonce();
  if (!sourceOrigin || !opener || !nonce) return () => {};

  let settled = false;
  const cleanup = () => {
    if (settled) return;
    settled = true;
    window.clearTimeout(timeout);
    window.removeEventListener("message", receiveAuth);
  };
  const receiveAuth = (event: MessageEvent<unknown>) => {
    if (event.origin !== sourceOrigin || event.source !== opener) return;
    if (!isMcpAuthHandoffMessage(event.data, nonce)) return;
    setMcpGatewayJwt(event.data.token);
    cleanup();
  };
  const timeout = window.setTimeout(cleanup, MCP_AUTH_HANDOFF_TIMEOUT_MS);

  window.addEventListener("message", receiveAuth);
  opener.postMessage({ type: MCP_AUTH_READY_MESSAGE, nonce }, sourceOrigin);
  return cleanup;
}

export function installMcpAuthHandoffLifecycle(
  authIncarnationEvent: string,
  installer: () => () => void = installMcpAuthHandoff,
): () => void {
  let cleanupHandoff = installer();
  const restart = () => {
    cleanupHandoff();
    cleanupHandoff = installer();
  };
  window.addEventListener(authIncarnationEvent, restart);
  return () => {
    window.removeEventListener(authIncarnationEvent, restart);
    cleanupHandoff();
  };
}
