import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export interface WorkbenchGovernance {
  projection: string;
  tenant_id: string;
  workspace_id: string;
  degraded: boolean;
  audit_required: boolean;
  rollback_available: boolean;
  secret_material_projected: boolean;
}

export interface WorkbenchUserProjection {
  id: string;
  username: string;
  email: string | null;
  full_name: string;
  is_active: boolean;
  is_superuser: boolean;
  roles: string[];
  permissions: string[];
  tenant_id: string;
  department_id: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface WorkbenchUserListResponse {
  users: WorkbenchUserProjection[];
  items: WorkbenchUserProjection[];
  total: number;
  skip: number;
  limit: number;
  governance: WorkbenchGovernance;
}

export interface WorkbenchSettingItem {
  key: string;
  value: unknown;
  type: string;
  category: string;
  label: string;
  description: string;
  is_public: boolean;
  is_secret: boolean;
  audit_required: boolean;
  rollback_available: boolean;
  updated_at: string | null;
}

export interface WorkbenchSettingGroup {
  category: string;
  items: WorkbenchSettingItem[];
}

export interface WorkbenchSettingsResponse {
  settings: Record<string, WorkbenchSettingGroup>;
  governance: WorkbenchGovernance;
}

export interface WorkbenchFeedbackStats {
  total_count: number;
  up_count: number;
  down_count: number;
  up_percentage: number;
}

export interface WorkbenchFeedbackItem {
  id: string;
  user_id: string;
  username: string;
  session_id: string;
  run_id: string;
  rating: string;
  comment: string | null;
  assignment_state: string;
  assignee_id: string | null;
  labels: string[];
  status: string;
  audit_history: Array<Record<string, unknown>>;
  created_at: string | null;
}

export interface WorkbenchFeedbackListResponse {
  items: WorkbenchFeedbackItem[];
  total: number;
  stats: WorkbenchFeedbackStats;
  governance: WorkbenchGovernance;
}

export interface WorkbenchI18nText {
  en: string;
  zh: string;
  ja: string;
  ko: string;
  ru: string;
}

export interface WorkbenchNotification {
  id: string;
  title_i18n: WorkbenchI18nText;
  content_i18n: WorkbenchI18nText;
  type: string;
  start_time: string | null;
  end_time: string | null;
  expires_at: string | null;
  is_active: boolean;
  read_state: string | null;
  audience?: Record<string, unknown> | null;
  audit_history: Array<Record<string, unknown>>;
  created_at: string | null;
  updated_at: string | null;
  created_by: string;
}

export interface WorkbenchNotificationListResponse {
  items: WorkbenchNotification[];
  total: number;
  governance: WorkbenchGovernance;
}

const WORKBENCH_USERS_API = `${API_BASE}/api/users/`;
const WORKBENCH_SETTINGS_API = `${API_BASE}/api/settings/`;
const WORKBENCH_FEEDBACK_API = `${API_BASE}/api/feedback/`;
const WORKBENCH_NOTIFICATIONS_ACTIVE_API = `${API_BASE}/api/notifications/active`;
const WORKBENCH_NOTIFICATIONS_ADMIN_API = `${API_BASE}/api/notifications/admin`;

function withQuery(url: string, params: Record<string, string | number | undefined>) {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") searchParams.set(key, String(value));
  }
  const query = searchParams.toString();
  return query ? `${url}?${query}` : url;
}

export const workbenchApi = {
  listUsers(params: { skip?: number; limit?: number; search?: string } = {}) {
    return authFetch<WorkbenchUserListResponse>(
      withQuery(WORKBENCH_USERS_API, params),
    );
  },

  listSettings() {
    return authFetch<WorkbenchSettingsResponse>(WORKBENCH_SETTINGS_API);
  },

  listFeedback(params: { skip?: number; limit?: number } = {}) {
    return authFetch<WorkbenchFeedbackListResponse>(
      withQuery(WORKBENCH_FEEDBACK_API, params),
    );
  },

  listActiveNotifications() {
    return authFetch<WorkbenchNotification[]>(WORKBENCH_NOTIFICATIONS_ACTIVE_API);
  },

  listAdminNotifications(params: { skip?: number; limit?: number } = {}) {
    return authFetch<WorkbenchNotificationListResponse>(
      withQuery(WORKBENCH_NOTIFICATIONS_ADMIN_API, params),
    );
  },
};
