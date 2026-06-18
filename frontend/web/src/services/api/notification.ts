import { authFetch } from "./fetch";
import type {
  Notification,
  NotificationCreate,
  NotificationListResponse,
  NotificationUpdate,
} from "../../types/notification";
import { API_BASE } from "./config";

interface AiPlatformActiveNotification {
  id?: string;
  title?: string;
  content?: string;
  level?: string;
  type?: string;
  created_at?: string;
  starts_at?: string;
  ends_at?: string;
}

type ActiveNotificationResponse =
  | Notification[]
  | { notifications?: AiPlatformActiveNotification[] };

const EMPTY_I18N = { en: "", zh: "", ja: "", ko: "", ru: "" };

function toNotificationType(value?: string): Notification["type"] {
  if (
    value === "info" ||
    value === "success" ||
    value === "warning" ||
    value === "maintenance"
  ) {
    return value;
  }
  return "info";
}

function normalizeActiveNotification(
  item: Notification | AiPlatformActiveNotification,
  index: number,
): Notification {
  if ("title_i18n" in item && item.title_i18n) return item;
  const active = item as AiPlatformActiveNotification;
  const title = active.title ?? `Notification ${index + 1}`;
  const content = active.content ?? "";
  return {
    id: active.id ?? `active-${index + 1}`,
    title_i18n: { ...EMPTY_I18N, en: title, zh: title },
    content_i18n: { ...EMPTY_I18N, en: content, zh: content },
    type: toNotificationType(active.type ?? active.level),
    start_time: active.starts_at ?? null,
    end_time: active.ends_at ?? null,
    is_active: true,
    created_at: active.created_at ?? "",
    updated_at: "",
    created_by: "",
  };
}

function normalizeActiveNotifications(
  response: ActiveNotificationResponse,
): Notification[] {
  const items = Array.isArray(response)
    ? response
    : (response.notifications ?? []);
  return items.map(normalizeActiveNotification);
}

export const notificationApi = {
  async getActive(): Promise<Notification[]> {
    try {
      const response = await authFetch<ActiveNotificationResponse>(
        `${API_BASE}/api/notifications/active`,
      );
      return normalizeActiveNotifications(response);
    } catch {
      return [];
    }
  },

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

  async dismiss(id: string): Promise<void> {
    return authFetch(`${API_BASE}/api/notifications/${id}/dismiss`, {
      method: "POST",
    });
  },
};
