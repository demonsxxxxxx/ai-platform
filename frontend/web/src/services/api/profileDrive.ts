import { authApi } from "./auth";

export interface ProfileDriveConnectionStatus {
  status: string;
  connected: boolean;
  reauthRequired: boolean;
  connectedAtUtc: string | null;
  lastUsedAtUtc: string | null;
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

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
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
  return (await response.json()) as T;
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
};
