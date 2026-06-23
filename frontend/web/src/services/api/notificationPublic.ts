import { authFetch } from "./fetch";
import type { Notification } from "../../types/notification";
import { API_BASE } from "./config";

export const notificationPublicApi = {
  async getActive(): Promise<Notification[]> {
    try {
      return await authFetch<Notification[]>(
        `${API_BASE}/api/notifications/active`,
      );
    } catch {
      return [];
    }
  },

  async dismiss(id: string): Promise<void> {
    return authFetch(`${API_BASE}/api/notifications/${id}/dismiss`, {
      method: "POST",
    });
  },
};
