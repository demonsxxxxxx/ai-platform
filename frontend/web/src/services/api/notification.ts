import { authFetch } from "./fetch";
import type {
  Notification,
  NotificationCreate,
  NotificationListResponse,
  NotificationUpdate,
} from "../../types/notification";
import { API_BASE } from "./config";

export const notificationApi = {
  async list(
    skip: number = 0,
    limit: number = 50,
  ): Promise<NotificationListResponse> {
    const params = new URLSearchParams({
      skip: skip.toString(),
      limit: limit.toString(),
    });
    return authFetch<NotificationListResponse>(
      `${API_BASE}/api/notifications/admin?${params}`,
    );
  },

  async create(data: NotificationCreate): Promise<Notification> {
    return authFetch<Notification>(`${API_BASE}/api/notifications/`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  async update(id: string, data: NotificationUpdate): Promise<Notification> {
    return authFetch<Notification>(`${API_BASE}/api/notifications/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
  },

  async delete(id: string): Promise<void> {
    return authFetch(`${API_BASE}/api/notifications/${id}`, {
      method: "DELETE",
    });
  },
};
