const SAFE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const ALLOWED_ROLES = new Set(["user", "admin"]);
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);

export const LOCAL_AUTH_PROXY_STRIPPED_HEADERS = Object.freeze([
  "X-AI-User-ID",
  "X-AI-User-Name",
  "X-AI-Tenant-ID",
  "X-AI-Department-ID",
  "X-AI-Roles",
  "X-AI-Permissions",
  "X-AI-Gateway-Secret",
]);

type Environment = Readonly<Record<string, string | undefined>>;

export interface LocalDevAuthProxyConfig {
  headers: Readonly<Record<string, string>>;
  serverHost: "127.0.0.1";
}

interface ResolveLocalDevAuthProxyOptions {
  apiTarget: string;
  command: string;
  env: Environment;
}

export type LocalDevAuthBootstrapResponse = {
  status: "ready";
  protocol_version: 1 | 2;
  generation?: number;
};

const BASE64URL_NONCE_PATTERN = /^[A-Za-z0-9_-]{43,512}$/;

/** Project the non-credential auth bootstrap shape used by the local proxy. */
export function localDevAuthBootstrapResponse(
  payload: unknown,
): LocalDevAuthBootstrapResponse | null {
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    return null;
  }
  const request = payload as Record<string, unknown>;
  const allowedKeys = new Set([
    "nonce",
    "protocol_version",
    "browser_incarnation",
    "generation",
    "rotation_ticket",
    "recovery_only",
  ]);
  if (Object.keys(request).some((key) => !allowedKeys.has(key))) {
    return null;
  }
  if (
    typeof request.nonce !== "string" ||
    !BASE64URL_NONCE_PATTERN.test(request.nonce)
  ) {
    return null;
  }

  const protocolVersion = request.protocol_version ?? 1;
  if (protocolVersion === 1) {
    if (
      request.browser_incarnation !== undefined ||
      request.generation !== undefined ||
      request.rotation_ticket !== undefined ||
      request.recovery_only === true
    ) {
      return null;
    }
    return { status: "ready", protocol_version: 1 };
  }
  if (
    protocolVersion !== 2 ||
    typeof request.browser_incarnation !== "string" ||
    !BASE64URL_NONCE_PATTERN.test(request.browser_incarnation) ||
    request.browser_incarnation.length !== 43 ||
    typeof request.generation !== "number" ||
    !Number.isSafeInteger(request.generation) ||
    request.generation < 1 ||
    (request.rotation_ticket !== undefined &&
      (typeof request.rotation_ticket !== "string" ||
        !BASE64URL_NONCE_PATTERN.test(request.rotation_ticket) ||
        request.rotation_ticket.length !== 43)) ||
    (request.recovery_only !== undefined &&
      typeof request.recovery_only !== "boolean")
  ) {
    return null;
  }
  return {
    status: "ready",
    protocol_version: 2,
    generation: request.generation,
  };
}

function safeIdentifier(
  env: Environment,
  name: string,
  fallback: string,
): string {
  const value = env[name]?.trim() || fallback;
  if (!SAFE_ID_PATTERN.test(value)) {
    throw new Error(
      `${name} must match the ai-platform safe identifier contract`,
    );
  }
  return value;
}

function assertLoopbackTarget(apiTarget: string): void {
  let target: URL;
  try {
    target = new URL(apiTarget);
  } catch {
    throw new Error("local auth proxy requires a valid API target URL");
  }
  if (target.protocol !== "http:" && target.protocol !== "https:") {
    throw new Error("local auth proxy requires an HTTP or HTTPS API target");
  }
  if (!LOOPBACK_HOSTS.has(target.hostname)) {
    throw new Error("local auth proxy may only target a loopback API address");
  }
}

/**
 * Resolve an explicitly enabled, loopback-only principal for the Vite dev proxy.
 *
 * The headers stay in the Node-side proxy and are never projected into browser
 * JavaScript or a production frontend image. The API must independently enable
 * FRONTEND_POC_AUTH_ENABLED before it will accept this principal.
 */
export function resolveLocalDevAuthProxy({
  apiTarget,
  command,
  env,
}: ResolveLocalDevAuthProxyOptions): LocalDevAuthProxyConfig | null {
  if (
    command !== "serve" ||
    env.AI_PLATFORM_LOCAL_AUTH_PROXY_ENABLED?.trim().toLowerCase() !== "true"
  ) {
    return null;
  }

  assertLoopbackTarget(apiTarget);
  const role = (env.AI_PLATFORM_LOCAL_AUTH_ROLE?.trim() || "user").toLowerCase();
  if (!ALLOWED_ROLES.has(role)) {
    throw new Error("AI_PLATFORM_LOCAL_AUTH_ROLE must be user or admin");
  }

  const headers: Record<string, string> = {
    "X-AI-User-ID": safeIdentifier(
      env,
      "AI_PLATFORM_LOCAL_AUTH_USER_ID",
      "local-dev-user",
    ),
    "X-AI-Tenant-ID": safeIdentifier(
      env,
      "AI_PLATFORM_LOCAL_AUTH_TENANT_ID",
      "default",
    ),
    "X-AI-Roles": role,
  };
  const departmentId = env.AI_PLATFORM_LOCAL_AUTH_DEPARTMENT_ID?.trim();
  if (departmentId) {
    headers["X-AI-Department-ID"] = safeIdentifier(
      env,
      "AI_PLATFORM_LOCAL_AUTH_DEPARTMENT_ID",
      departmentId,
    );
  }

  return {
    headers: Object.freeze(headers),
    serverHost: "127.0.0.1",
  };
}
