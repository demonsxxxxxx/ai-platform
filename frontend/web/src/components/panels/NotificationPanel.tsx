/**
 * 通知管理面板 - Admin CRUD panel for notifications
 */

import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import {
  Plus,
  Pencil,
  Trash2,
  Bell,
  X,
  AlertCircle,
  ChevronDown,
} from "lucide-react";
import { PanelHeader } from "../common/PanelHeader";
import { Pagination } from "../common/Pagination";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { notificationApi } from "../../services/api/notification";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import type {
  Notification,
  NotificationCreate,
} from "../../types/notification";
import type { I18nText } from "../../types/notification";
import { formatDateTimeShort, parseDate } from "../../utils/datetime";

const LOCALE_KEYS: Array<{ key: keyof I18nText; label: string }> = [
  { key: "en", label: "English" },
  { key: "zh", label: "中文" },
  { key: "ja", label: "日本語" },
  { key: "ko", label: "한국어" },
  { key: "ru", label: "Русский" },
];

const emptyI18n: I18nText = { en: "", zh: "", ja: "", ko: "", ru: "" };

/** Convert ISO datetime string to datetime-local input value (YYYY-MM-DDTHH:mm) */
function toDatetimeLocal(value: string | null): string {
  if (!value) return "";
  const d = parseDate(value);
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

/** Convert datetime-local input value to ISO string */
function fromDatetimeLocal(value: string): string | null {
  if (!value) return null;
  return new Date(value).toISOString();
}

/** Compute display status based on is_active and schedule */
function getNotificationStatus(
  notification: Notification,
): "active" | "inactive" | "scheduled" | "expired" {
  const now = Date.now();
  if (!notification.is_active) return "inactive";
  if (
    notification.end_time &&
    parseDate(notification.end_time).getTime() < now
  ) {
    return "expired";
  }
  if (
    notification.start_time &&
    parseDate(notification.start_time).getTime() > now
  ) {
    return "scheduled";
  }
  return "active";
}

/** Status badge component */
function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation();

  const styles: Record<string, string> = {
    active:
      "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
    inactive:
      "bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-400",
    scheduled:
      "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
    expired: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  };

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
        styles[status] || styles.inactive
      }`}
    >
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full ${
          status === "active"
            ? "bg-emerald-500"
            : status === "scheduled"
              ? "bg-blue-500"
              : status === "expired"
                ? "bg-red-500"
                : "bg-stone-400"
        }`}
      />
      {t(`notification.${status}`)}
    </span>
  );
}

/** Delete confirmation modal */
function DeleteConfirmModal({
  onConfirm,
  onCancel,
}: {
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();

  return (
    <>
      <div
        className="enterprise-modal-backdrop"
        onClick={onCancel}
      />
      <div className="enterprise-modal-layer">
        <div
          className="enterprise-modal-shell p-5"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-lg border border-red-200 bg-red-50 dark:border-red-900/50 dark:bg-red-950/30">
            <AlertCircle className="text-red-600 dark:text-red-400" size={24} />
          </div>

          <h3 className="text-base font-semibold text-stone-900 dark:text-stone-100">
            {t("notification.deleteConfirm")}
          </h3>

          <div className="mt-2">
            <p className="text-sm text-stone-500 dark:text-stone-400">
              {t("common.confirmAction") || "This action cannot be undone."}
            </p>
          </div>

          <div className="mt-6 flex gap-3">
            <button
              onClick={onCancel}
              className="btn-secondary flex-1 justify-center"
            >
              {t("notification.cancel")}
            </button>
            <button
              onClick={onConfirm}
              className="btn-danger flex-1 justify-center border-red-300 bg-red-600 text-white hover:bg-red-700 dark:border-red-700 dark:bg-red-600 dark:text-white dark:hover:bg-red-700"
            >
              {t("notification.delete")}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

/** Create/Edit modal */
function NotificationFormModal({
  notification,
  onSave,
  onClose,
}: {
  notification: Notification | null;
  onSave: (data: NotificationCreate) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const isEdit = !!notification;

  const [titleI18n, setTitleI18n] = useState<I18nText>(
    notification?.title_i18n ?? { ...emptyI18n },
  );
  const [contentI18n, setContentI18n] = useState<I18nText>(
    notification?.content_i18n ?? { ...emptyI18n },
  );
  const [startTime, setStartTime] = useState(
    toDatetimeLocal(notification?.start_time ?? null),
  );
  const [endTime, setEndTime] = useState(
    toDatetimeLocal(notification?.end_time ?? null),
  );
  const [isActive, setIsActive] = useState(notification?.is_active ?? true);
  const [notifType, setNotifType] = useState<string>(
    notification?.type ?? "info",
  );
  const [isSaving, setIsSaving] = useState(false);

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const data: NotificationCreate = {
        title_i18n: titleI18n,
        content_i18n: contentI18n,
        type: notifType as NotificationCreate["type"],
        start_time: fromDatetimeLocal(startTime),
        end_time: fromDatetimeLocal(endTime),
        is_active: isActive,
      };
      await onSave(data);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <>
      <div
        className="enterprise-modal-backdrop"
        onClick={onClose}
      />
      <div className="enterprise-modal-layer">
        <div
          className="enterprise-modal-shell enterprise-modal-shell--wide enterprise-modal-shell--scroll"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="enterprise-modal-header">
            <h3 className="text-base font-semibold text-stone-900 dark:text-stone-100">
              {isEdit ? t("notification.edit") : t("notification.create")}
            </h3>
            <button
              onClick={onClose}
              className="enterprise-icon-button"
            >
              <X size={20} />
            </button>
          </div>

          <div className="enterprise-modal-body space-y-6">
            {/* Title fields for each language */}
            <div>
              <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-3">
                {t("notification.titleLabel")}
              </label>
              <div className="space-y-3">
                {LOCALE_KEYS.map(({ key, label }) => (
                  <div key={key}>
                    <label className="block text-xs text-stone-500 dark:text-stone-400 mb-1">
                      {label}
                    </label>
                    <input
                      type="text"
                      value={titleI18n[key]}
                      onChange={(e) =>
                        setTitleI18n((prev) => ({
                          ...prev,
                          [key]: e.target.value,
                        }))
                      }
                      className="enterprise-form-input"
                      placeholder={`${label} title`}
                    />
                  </div>
                ))}
              </div>
            </div>

            {/* Content fields for each language */}
            <div>
              <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-3">
                {t("notification.contentLabel")}
              </label>
              <div className="space-y-3">
                {LOCALE_KEYS.map(({ key, label }) => (
                  <div key={key}>
                    <label className="block text-xs text-stone-500 dark:text-stone-400 mb-1">
                      {label}
                    </label>
                    <textarea
                      value={contentI18n[key]}
                      onChange={(e) =>
                        setContentI18n((prev) => ({
                          ...prev,
                          [key]: e.target.value,
                        }))
                      }
                      rows={3}
                      className="enterprise-form-textarea"
                      placeholder={`${label} content`}
                    />
                  </div>
                ))}
              </div>
            </div>

            {/* Type selector */}
            <div>
              <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-1.5">
                {t("notification.typeLabel")}
              </label>
              <div className="flex flex-wrap gap-2">
                {(["info", "success", "warning", "maintenance"] as const).map(
                  (nt) => (
                    <button
                      key={nt}
                      type="button"
                      onClick={() => setNotifType(nt)}
                      className={`rounded-lg border px-3 py-2 text-xs font-medium transition-all ${
                        notifType === nt
                          ? nt === "info"
                            ? "border-blue-400 bg-blue-50 text-blue-700 dark:border-blue-500 dark:bg-blue-900/30 dark:text-blue-300"
                            : nt === "success"
                              ? "border-emerald-400 bg-emerald-50 text-emerald-700 dark:border-emerald-500 dark:bg-emerald-900/30 dark:text-emerald-300"
                              : nt === "warning"
                                ? "border-amber-400 bg-amber-50 text-amber-700 dark:border-amber-500 dark:bg-amber-900/30 dark:text-amber-300"
                                : "border-orange-400 bg-orange-50 text-orange-700 dark:border-orange-500 dark:bg-orange-900/30 dark:text-orange-300"
                          : "border-[var(--theme-border)] bg-[var(--theme-bg)] text-[var(--theme-text-secondary)] hover:border-stone-300 dark:hover:border-stone-600"
                      }`}
                    >
                      {t(
                        `notification.type${
                          nt.charAt(0).toUpperCase() + nt.slice(1)
                        }`,
                      )}
                    </button>
                  ),
                )}
              </div>
            </div>

            {/* Schedule */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-1.5">
                  {t("notification.startTime")}
                </label>
                <input
                  type="datetime-local"
                  value={startTime}
                  onChange={(e) => setStartTime(e.target.value)}
                  className="enterprise-form-input"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-1.5">
                  {t("notification.endTime")}
                </label>
                <input
                  type="datetime-local"
                  value={endTime}
                  onChange={(e) => setEndTime(e.target.value)}
                  className="enterprise-form-input"
                />
              </div>
            </div>

            {/* Active toggle */}
            <div className="flex items-center justify-between rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg)] p-4">
              <div>
                <p className="text-sm font-medium text-stone-700 dark:text-stone-300">
                  {t("notification.isActive")}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setIsActive(!isActive)}
                className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--theme-ring)]/20 ${
                  isActive ? "bg-emerald-500" : "bg-stone-300 dark:bg-stone-600"
                }`}
              >
                <span
                  className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                    isActive ? "translate-x-5" : "translate-x-0"
                  }`}
                />
              </button>
            </div>
          </div>

          {/* Footer */}
          <div className="enterprise-modal-footer">
            <button
              onClick={onClose}
              className="btn-secondary flex-1 justify-center"
            >
              {t("notification.cancel")}
            </button>
            <button
              onClick={handleSave}
              disabled={isSaving}
              className="btn-primary flex-1 justify-center"
            >
              {isSaving ? (
                <span className="inline-flex items-center gap-2">
                  <svg
                    className="h-4 w-4 animate-spin"
                    viewBox="0 0 24 24"
                    fill="none"
                  >
                    <circle
                      className="opacity-25"
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeWidth="4"
                    />
                    <path
                      className="opacity-75"
                      fill="currentColor"
                      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                    />
                  </svg>
                  {t("common.saving") || "Saving..."}
                </span>
              ) : (
                t("notification.save")
              )}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

export function NotificationPanel() {
  const { t, i18n } = useTranslation();
  const { hasPermission } = useAuth();
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [limit] = useState(20);
  const [deleteTarget, setDeleteTarget] = useState<Notification | null>(null);
  const [editingNotification, setEditingNotification] =
    useState<Notification | null>(null);
  const [isCreating, setIsCreating] = useState(false);

  const canManage = hasPermission(Permission.NOTIFICATION_MANAGE);

  // Fetch notifications
  const fetchNotifications = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await notificationApi.list(skip, limit);
      setNotifications(response.items);
      setTotal(response.total);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : t("common.loadFailed");
      toast.error(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, limit, t]);

  // Initial load
  useEffect(() => {
    fetchNotifications();
  }, [fetchNotifications]);

  // Handle create
  const handleCreate = async (data: NotificationCreate) => {
    try {
      await notificationApi.create(data);
      toast.success(t("notification.createdSuccess"));
      setIsCreating(false);
      fetchNotifications();
    } catch (error) {
      const message =
        error instanceof Error ? error.message : t("common.saveFailed");
      toast.error(message);
    }
  };

  // Handle update
  const handleUpdate = async (data: NotificationCreate) => {
    if (!editingNotification) return;
    try {
      await notificationApi.update(editingNotification.id, data);
      toast.success(t("notification.updatedSuccess"));
      setEditingNotification(null);
      fetchNotifications();
    } catch (error) {
      const message =
        error instanceof Error ? error.message : t("common.saveFailed");
      toast.error(message);
    }
  };

  // Handle delete
  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await notificationApi.delete(deleteTarget.id);
      toast.success(t("notification.deletedSuccess"));
      setDeleteTarget(null);
      fetchNotifications();
    } catch (error) {
      const message =
        error instanceof Error ? error.message : t("common.deleteFailed");
      toast.error(message);
    }
  };

  // Get localized title with fallback
  const getLocalizedTitle = (notification: Notification): string => {
    const locale = (i18n.language || "en").split("-")[0];
    return (
      notification.title_i18n[locale as keyof I18nText] ||
      notification.title_i18n.en ||
      ""
    );
  };

  // Get localized content with fallback
  const getLocalizedContent = (notification: Notification): string => {
    const locale = (i18n.language || "en").split("-")[0];
    return (
      notification.content_i18n[locale as keyof I18nText] ||
      notification.content_i18n.en ||
      ""
    );
  };

  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Format schedule info
  const formatSchedule = (notification: Notification): string => {
    if (notification.start_time && notification.end_time) {
      return `${formatDateTimeShort(
        notification.start_time,
      )} - ${formatDateTimeShort(notification.end_time)}`;
    }
    if (notification.start_time) {
      return `${t("notification.startTime")}: ${formatDateTimeShort(
        notification.start_time,
      )}`;
    }
    if (notification.end_time) {
      return `${t("notification.endTime")}: ${formatDateTimeShort(
        notification.end_time,
      )}`;
    }
    return "";
  };

  // Permission denied
  if (!canManage) {
    return (
      <div className="flex h-full items-center justify-center bg-[var(--theme-bg)] p-6">
        <WorkbenchStateSurface
          state="forbidden"
          surface="notifications-route-permission"
          title={t("common.accessDenied") || "Access Denied"}
          description={
            t("common.permissionRequired") ||
            "You do not have permission to manage notifications."
          }
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--theme-bg)] text-slate-950 dark:bg-stone-950 dark:text-stone-100">
      {/* Header */}
      <PanelHeader
        title={t("notification.title")}
        icon={<Bell size={20} className="text-stone-600 dark:text-stone-400" />}
        actions={
          <button
            onClick={() => setIsCreating(true)}
            className="btn-primary"
          >
            <Plus size={16} />
            {t("notification.create")}
          </button>
        }
      />

      {/* Notification List */}
      <div className="flex-1 overflow-y-auto py-2 sm:py-4 px-4 sm:p-6">
        {isLoading && notifications.length === 0 ? (
          <div className="flex h-40 items-center justify-center">
            <div className="relative h-8 w-8">
              <div className="absolute inset-0 rounded-full border-2 border-stone-200 dark:border-stone-700" />
              <div className="absolute inset-0 rounded-full border-2 border-transparent border-t-stone-600 dark:border-t-stone-300 animate-spin will-change-transform" />
            </div>
          </div>
        ) : !isLoading && notifications.length === 0 ? (
          <div className="enterprise-empty-state">
            <div className="enterprise-empty-state-icon mb-4">
              <Bell size={32} className="text-stone-400 dark:text-stone-500" />
            </div>
            <p className="text-lg font-medium text-stone-700 dark:text-stone-300">
              {t("notification.noNotifications")}
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {notifications.map((notification) => {
              const status = getNotificationStatus(notification);
              const schedule = formatSchedule(notification);
              const content = getLocalizedContent(notification);
              const isExpanded = expandedId === notification.id;
              const hasContent = content.length > 0;

              return (
                <div
                  key={notification.id}
                  className="panel-card p-4 transition-colors hover:border-stone-300 dark:hover:border-stone-600 sm:p-5"
                >
                  <div className="flex items-start justify-between gap-3 sm:gap-4">
                    {/* Info */}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 sm:gap-3 mb-2">
                        <span
                          className={`inline-flex items-center gap-1 shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase leading-none ${
                            notification.type === "info"
                              ? "bg-blue-500/15 text-blue-600 dark:text-blue-300"
                              : notification.type === "success"
                                ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300"
                                : notification.type === "warning"
                                  ? "bg-amber-500/15 text-amber-600 dark:text-amber-300"
                                  : "bg-orange-500/15 text-orange-600 dark:text-orange-300"
                          }`}
                        >
                          {t(
                            `notification.type${
                              notification.type.charAt(0).toUpperCase() +
                              notification.type.slice(1)
                            }`,
                          )}
                        </span>
                        <p className="font-medium text-stone-900 dark:text-stone-100 break-words line-clamp-1">
                          {getLocalizedTitle(notification)}
                        </p>
                      </div>
                      <div className="flex items-center gap-2 mb-2">
                        <StatusBadge status={status} />
                      </div>
                      {schedule && (
                        <p className="text-xs text-stone-500 dark:text-stone-400 mb-2">
                          {schedule}
                        </p>
                      )}
                      <p className="text-xs text-stone-400 dark:text-stone-500">
                        {formatDateTimeShort(notification.created_at)}
                      </p>
                      {/* Expandable content */}
                      {hasContent && (
                        <div
                          className={`mt-2 text-xs leading-relaxed text-stone-600 dark:text-stone-400 overflow-hidden transition-all duration-200 ${
                            isExpanded
                              ? "max-h-96 opacity-100"
                              : "max-h-0 opacity-0"
                          }`}
                        >
                          <div
                            className="pt-2 border-t"
                            style={{ borderColor: "var(--theme-border)" }}
                          >
                            {content}
                          </div>
                        </div>
                      )}
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-1 flex-shrink-0">
                      {hasContent && (
                        <button
                          onClick={() =>
                            setExpandedId(isExpanded ? null : notification.id)
                          }
                          className={`flex h-9 w-9 items-center justify-center rounded-lg transition-all ${
                            isExpanded
                              ? "bg-[var(--theme-bg-sidebar)] text-stone-600 dark:text-stone-300"
                              : "enterprise-icon-button"
                          }`}
                          title={
                            isExpanded
                              ? t("notification.collapse")
                              : t("notification.expand")
                          }
                        >
                          <ChevronDown
                            size={16}
                            className={`transition-transform duration-200 ${
                              isExpanded ? "rotate-180" : ""
                            }`}
                          />
                        </button>
                      )}
                      <button
                        onClick={() => setEditingNotification(notification)}
                        className="enterprise-icon-button"
                        title={t("notification.edit")}
                      >
                        <Pencil size={16} />
                      </button>
                      <button
                        onClick={() => setDeleteTarget(notification)}
                        className="enterprise-danger-icon-button"
                        title={t("notification.delete")}
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Pagination */}
      {total > limit && (
        <div className="enterprise-divider border-t bg-transparent px-4 py-4 sm:px-6">
          <Pagination
            page={Math.floor(skip / limit) + 1}
            pageSize={limit}
            total={total}
            onChange={(page) => setSkip((page - 1) * limit)}
          />
        </div>
      )}

      {/* Create Modal */}
      {isCreating && (
        <NotificationFormModal
          notification={null}
          onSave={handleCreate}
          onClose={() => setIsCreating(false)}
        />
      )}

      {/* Edit Modal */}
      {editingNotification && (
        <NotificationFormModal
          notification={editingNotification}
          onSave={handleUpdate}
          onClose={() => setEditingNotification(null)}
        />
      )}

      {/* Delete Confirmation Modal */}
      {deleteTarget && (
        <DeleteConfirmModal
          onConfirm={handleDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
