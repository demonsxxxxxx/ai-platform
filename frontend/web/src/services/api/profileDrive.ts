import { authApi } from "./auth";

export interface ProfileDriveConnectionStatus {
  status: string;
  connected: boolean;
  reauthRequired: boolean;
  connectedAtUtc: string | null;
  lastUsedAtUtc: string | null;
}

export interface ProfileDriveFileEntry {
  path: string;
  name: string;
  type: "file" | "directory";
  size: number | null;
  lastModifiedUtc: string;
}

export interface ProfileDriveListResult {
  status: string;
  path: string;
  entries: ProfileDriveFileEntry[];
  truncated: boolean;
}

export class ProfileDriveRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super("Profile drive request failed.");
    this.name = "ProfileDriveRequestError";
  }
}

async function authorizedRequest(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const credential = await authApi.getCompanyCredentialForHandoff();
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  headers.set("Authorization", `Bearer ${credential}`);
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`/api/profile-drive${path}`, {
    ...options,
    credentials: "omit",
    headers,
  });
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    const code =
      payload && typeof payload === "object" && !Array.isArray(payload)
        ? String((payload as { status?: unknown }).status || "request_failed")
        : "request_failed";
    throw new ProfileDriveRequestError(response.status, code);
  }
  return response;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  return (await authorizedRequest(path, options)).json() as Promise<T>;
}

function profileDriveListResult(value: unknown): ProfileDriveListResult {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ProfileDriveRequestError(502, "invalid_response");
  }
  const record = value as Record<string, unknown>;
  if (
    typeof record.status !== "string" ||
    typeof record.path !== "string" ||
    typeof record.truncated !== "boolean" ||
    !Array.isArray(record.entries) ||
    record.entries.length > 500
  ) {
    throw new ProfileDriveRequestError(502, "invalid_response");
  }
  const entries = record.entries.map((entry) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new ProfileDriveRequestError(502, "invalid_response");
    }
    const item = entry as Record<string, unknown>;
    if (
      typeof item.path !== "string" ||
      typeof item.name !== "string" ||
      !["file", "directory"].includes(String(item.type)) ||
      (item.size !== null &&
        (typeof item.size !== "number" || !Number.isSafeInteger(item.size) || item.size < 0)) ||
      typeof item.lastModifiedUtc !== "string"
    ) {
      throw new ProfileDriveRequestError(502, "invalid_response");
    }
    return {
      path: item.path,
      name: item.name,
      type: item.type as ProfileDriveFileEntry["type"],
      size: item.size as number | null,
      lastModifiedUtc: item.lastModifiedUtc,
    };
  });
  return {
    status: record.status,
    path: record.path,
    entries,
    truncated: record.truncated,
  };
}

export const profileDriveApi = {
  connect(password: string) {
    return request<ProfileDriveConnectionStatus>("/connect", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
  },

  status() {
    return request<ProfileDriveConnectionStatus>("/status", {
      cache: "no-store",
    });
  },

  async listFiles(path = "") {
    return profileDriveListResult(
      await request<unknown>("/files/list", {
        method: "POST",
        body: JSON.stringify({ path, maxEntries: 200 }),
      }),
    );
  },
};
