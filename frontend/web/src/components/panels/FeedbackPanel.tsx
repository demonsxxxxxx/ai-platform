/**
 * 反馈管理面板 - enterprise workbench surface
 */

import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import {
  ThumbsUp,
  ThumbsDown,
  Trash2,
  AlertCircle,
  MessageSquare,
  Star,
  TrendingUp,
  Copy,
  Check,
  X,
} from "lucide-react";
import { PanelHeader } from "../common/PanelHeader";
import { EnterpriseSelect } from "../common/EnterpriseSelect";
import { FeedbackPanelSkeleton } from "../skeletons";
import { Pagination } from "../common/Pagination";
import { feedbackApi } from "../../services/api/feedback";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import type {
  Feedback,
  FeedbackStats,
  RatingValue,
} from "../../types/feedback";
import { formatDateTimeShort, formatDateTime } from "../../utils/datetime";
import { copyToClipboard } from "../../utils/clipboard";

// Stats card component
function StatsCard({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
}) {
  return (
    <div className="panel-card">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)]">
          <Icon size={24} className="text-stone-600 dark:text-stone-400" />
        </div>
        <div>
          <p className="text-xs text-stone-500 dark:text-stone-400">{label}</p>
          <p className="text-xl font-bold text-stone-900 dark:text-stone-100">
            {value}
          </p>
        </div>
      </div>
    </div>
  );
}

// Delete confirmation modal
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
            {t("feedback.deleteConfirmTitle")}
          </h3>

          <div className="mt-2">
            <p className="text-sm text-stone-500 dark:text-stone-400">
              {t("feedback.deleteConfirm")}
            </p>
          </div>

          <div className="mt-6 flex gap-3">
            <button
              onClick={onCancel}
              className="btn-secondary flex-1 justify-center"
            >
              {t("common.cancel")}
            </button>
            <button
              onClick={onConfirm}
              className="btn-danger flex-1 justify-center border-red-300 bg-red-600 text-white hover:bg-red-700 dark:border-red-700 dark:bg-red-600 dark:text-white dark:hover:bg-red-700"
            >
              {t("feedback.delete")}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

// Feedback detail modal
function FeedbackDetailModal({
  feedback,
  onClose,
  onCopy,
  copiedField,
}: {
  feedback: Feedback;
  onClose: () => void;
  onCopy: (text: string, field: string) => void;
  copiedField: string | null;
}) {
  const { t } = useTranslation();

  return (
    <>
      <div
        className="enterprise-modal-backdrop"
        onClick={onClose}
      />
      <div className="enterprise-modal-layer">
        <div
          className="enterprise-modal-shell enterprise-modal-shell--wide"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="enterprise-modal-header">
            <h3 className="text-base font-semibold text-stone-900 dark:text-stone-100">
              {t("feedback.detailTitle") || "Feedback Details"}
            </h3>
            <button
              onClick={onClose}
              className="enterprise-icon-button"
            >
              <span className="sr-only">Close</span>
              <X size={18} />
            </button>
          </div>

          <div className="enterprise-modal-body">
            <div className="flex items-center gap-3 mb-4">
              <div className="enterprise-avatar h-12 w-12 text-lg">
                {feedback.username.charAt(0).toUpperCase()}
              </div>
              <div>
                <p className="font-medium text-stone-900 dark:text-stone-100">
                  {feedback.username}
                </p>
                <p className="text-sm text-stone-500 dark:text-stone-400">
                  {formatDateTime(feedback.created_at)}
                </p>
              </div>
            </div>

            {/* Rating */}
            <div className="flex items-center gap-2 mb-6">
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium ${
                  feedback.rating === "up"
                    ? "bg-[var(--theme-bg-sidebar)] text-stone-600 ring-1 ring-[var(--theme-border)] dark:text-stone-300"
                    : "bg-stone-800 text-stone-300 dark:bg-stone-200 dark:text-stone-700"
                }`}
              >
                {feedback.rating === "up" ? (
                  <ThumbsUp size={16} />
                ) : (
                  <ThumbsDown size={16} />
                )}
                {feedback.rating === "up"
                  ? t("feedback.positive")
                  : t("feedback.negative")}
              </span>
            </div>

            {/* Session & Run IDs */}
            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-stone-500 dark:text-stone-400 mb-1">
                  Session ID
                </label>
                <div className="flex items-center gap-2">
                  <code className="enterprise-code-chip flex-1 truncate">
                    {feedback.session_id}
                  </code>
                  <button
                    onClick={() => onCopy(feedback.session_id, "session")}
                    className="enterprise-icon-button"
                    title={t("documents.copy")}
                  >
                    {copiedField === "session" ? (
                      <Check size={16} />
                    ) : (
                      <Copy size={16} />
                    )}
                  </button>
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-stone-500 dark:text-stone-400 mb-1">
                  Run ID
                </label>
                <div className="flex items-center gap-2">
                  <code className="enterprise-code-chip flex-1 truncate">
                    {feedback.run_id}
                  </code>
                  <button
                    onClick={() => onCopy(feedback.run_id, "run")}
                    className="enterprise-icon-button"
                    title={t("documents.copy")}
                  >
                    {copiedField === "run" ? (
                      <Check size={16} />
                    ) : (
                      <Copy size={16} />
                    )}
                  </button>
                </div>
              </div>
            </div>
          {/* Comment */}
          {feedback.comment && (
            <div>
              <label className="block text-xs font-medium text-stone-500 dark:text-stone-400 mb-2">
                {t("feedback.comment") || "Comment"}
              </label>
              <div className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] p-4 text-sm text-stone-700 dark:text-stone-300 whitespace-pre-wrap">
                {feedback.comment}
              </div>
            </div>
          )}
          </div>

          <div className="enterprise-modal-footer justify-end">
            <button
              onClick={onClose}
              className="btn-secondary"
            >
              {t("common.close") || "Close"}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

// Helper function removed - using formatDateTime from shared utility

export function FeedbackPanel() {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const [feedbackList, setFeedbackList] = useState<Feedback[]>([]);
  const [stats, setStats] = useState<FeedbackStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [limit] = useState(20);
  const [ratingFilter, setRatingFilter] = useState<RatingValue | undefined>(
    undefined,
  );
  const [deleteTarget, setDeleteTarget] = useState<Feedback | null>(null);
  const [selectedFeedback, setSelectedFeedback] = useState<Feedback | null>(
    null,
  );
  const [copiedField, setCopiedField] = useState<string | null>(null);

  const canDelete = hasPermission(Permission.FEEDBACK_ADMIN);

  // Copy to clipboard
  const handleCopy = async (text: string, field: string) => {
    try {
      await copyToClipboard(text);
      setCopiedField(field);
      setTimeout(() => setCopiedField(null), 2000);
    } catch (error) {
      console.error("Failed to copy:", error);
    }
  };

  // Fetch feedback data
  const fetchFeedback = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await feedbackApi.list(skip, limit, ratingFilter);
      setFeedbackList(response.items);
      setStats(response.stats);
      setTotal(response.total);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : t("common.loadFailed");
      toast.error(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, limit, ratingFilter, t]);

  // Initial load
  useEffect(() => {
    fetchFeedback();
  }, [fetchFeedback]);

  // Reset to first page when filters change
  useEffect(() => {
    setSkip(0);
  }, [ratingFilter]);

  // Handle delete
  const handleDelete = async () => {
    if (!deleteTarget) return;

    try {
      await feedbackApi.delete(deleteTarget.id);
      toast.success(t("feedback.deleteSuccess"));
      setDeleteTarget(null);
      fetchFeedback();
    } catch (error) {
      const message =
        error instanceof Error ? error.message : t("feedback.deleteFailed");
      toast.error(message);
    }
  };

  // Format date - using shared utility
  const formatDateLocal = (dateString: string) =>
    formatDateTimeShort(dateString);

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--theme-bg)] text-slate-950 dark:bg-stone-950 dark:text-stone-100">
      {/* Header */}
      <PanelHeader
        title={t("feedback.title")}
        subtitle={t("feedback.subtitle")}
        icon={<Star size={20} className="text-stone-600 dark:text-stone-400" />}
        actions={
          <div className="w-full sm:w-44">
            <EnterpriseSelect
              value={ratingFilter || ""}
              onChange={(v) =>
                setRatingFilter(v ? (v as RatingValue) : undefined)
              }
              placeholder={t("feedback.allRatings")}
              options={[
                { value: "", label: t("feedback.allRatings") },
                { value: "up", label: t("feedback.positive") },
                { value: "down", label: t("feedback.negative") },
              ]}
            />
          </div>
        }
      />

      {/* Stats Section - Modern card design */}
      {stats && (
        <div className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-4 sm:gap-4">
          <StatsCard
            icon={MessageSquare}
            label={t("feedback.totalCount")}
            value={stats.total_count}
          />
          <StatsCard
            icon={ThumbsUp}
            label={t("feedback.positive")}
            value={stats.up_count}
          />
          <StatsCard
            icon={ThumbsDown}
            label={t("feedback.negative")}
            value={stats.down_count}
          />
          <StatsCard
            icon={TrendingUp}
            label={t("feedback.positiveRate")}
            value={`${stats.up_percentage.toFixed(1)}%`}
          />
        </div>
      )}

      {/* Feedback List */}
      <div className="flex-1 overflow-y-auto py-2 sm:py-4 px-4 sm:p-6">
        {isLoading && feedbackList.length === 0 ? (
          <FeedbackPanelSkeleton />
        ) : !isLoading && feedbackList.length === 0 ? (
          <div className="enterprise-empty-state">
            <div className="enterprise-empty-state-icon mb-4">
              <ThumbsUp
                size={32}
                className="text-stone-400 dark:text-stone-500"
              />
            </div>
            <p className="text-lg font-medium text-stone-700 dark:text-stone-300">
              {t("feedback.noFeedback")}
            </p>
            <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
              {t("feedback.noFeedbackHint")}
            </p>
          </div>
        ) : (
          <>
            {/* Mobile card view - modern style */}
            <div className="space-y-3 sm:hidden">
              {feedbackList.map((feedback) => (
                <button
                  key={feedback.id}
                  onClick={() => setSelectedFeedback(feedback)}
                  className="panel-card group relative w-full overflow-hidden text-left"
                >
                  <div className="flex items-start justify-between gap-3">
                    {/* User Info */}
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="enterprise-avatar h-9 w-9">
                        {feedback.username.charAt(0).toUpperCase()}
                      </div>
                      <div className="min-w-0">
                        <p className="truncate font-medium text-stone-900 dark:text-stone-100">
                          {feedback.username}
                        </p>
                        <p className="text-xs text-stone-400 dark:text-stone-500">
                          {formatDateLocal(feedback.created_at)}
                        </p>
                      </div>
                    </div>
                    {/* Rating & Actions */}
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <span
                        className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${
                          feedback.rating === "up"
                            ? "bg-[var(--theme-bg-sidebar)] text-stone-600 ring-1 ring-[var(--theme-border)] dark:text-stone-300"
                            : "bg-stone-800 text-stone-300 dark:bg-stone-200 dark:text-stone-700"
                        }`}
                      >
                        {feedback.rating === "up" ? (
                          <ThumbsUp size={12} />
                        ) : (
                          <ThumbsDown size={12} />
                        )}
                      </span>
                      {canDelete && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setDeleteTarget(feedback);
                          }}
                          className="enterprise-danger-icon-button h-8 w-8"
                          title={t("feedback.delete")}
                        >
                          <Trash2 size={16} />
                        </button>
                      )}
                    </div>
                  </div>
                  {/* Comment */}
                  {feedback.comment && (
                    <div className="mt-3 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] p-3 text-sm text-stone-600 dark:text-stone-300 whitespace-pre-wrap">
                      {feedback.comment}
                    </div>
                  )}
                </button>
              ))}
            </div>

            {/* Desktop card view - modern style */}
            <div className="hidden space-y-3 sm:block">
              {feedbackList.map((feedback) => (
                <button
                  key={feedback.id}
                  onClick={() => setSelectedFeedback(feedback)}
                  className="panel-card group relative w-full overflow-hidden p-5 text-left"
                >
                  <div className="flex items-start justify-between gap-4">
                    {/* User Info */}
                    <div className="flex items-center gap-4">
                      <div className="enterprise-avatar">
                        {feedback.username.charAt(0).toUpperCase()}
                      </div>
                      <div>
                        <p className="font-medium text-stone-900 dark:text-stone-100">
                          {feedback.username}
                        </p>
                        <p className="text-xs text-stone-400 dark:text-stone-500">
                          {formatDateLocal(feedback.created_at)}
                        </p>
                      </div>
                    </div>

                    {/* Rating Badge */}
                    <span
                      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium ${
                        feedback.rating === "up"
                          ? "bg-[var(--theme-bg-sidebar)] text-stone-600 ring-1 ring-[var(--theme-border)] dark:text-stone-300"
                          : "bg-stone-800 text-stone-300 dark:bg-stone-200 dark:text-stone-700"
                      }`}
                    >
                      {feedback.rating === "up" ? (
                        <ThumbsUp size={14} />
                      ) : (
                        <ThumbsDown size={14} />
                      )}
                      {feedback.rating === "up"
                        ? t("feedback.positive")
                        : t("feedback.negative")}
                    </span>

                    {/* Delete Button */}
                    {canDelete && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setDeleteTarget(feedback);
                        }}
                        className="enterprise-danger-icon-button opacity-0 group-hover:opacity-100"
                        title={t("feedback.delete")}
                      >
                        <Trash2 size={18} />
                      </button>
                    )}
                  </div>

                  {/* Comment */}
                  {feedback.comment && (
                    <div className="mt-4 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] p-4 text-sm text-stone-600 dark:text-stone-300 whitespace-pre-wrap">
                      {feedback.comment}
                    </div>
                  )}
                </button>
              ))}
            </div>
          </>
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

      {/* Delete Confirmation Modal */}
      {deleteTarget && (
        <DeleteConfirmModal
          onConfirm={handleDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}

      {/* Feedback Detail Modal */}
      {selectedFeedback && (
        <FeedbackDetailModal
          feedback={selectedFeedback}
          onClose={() => setSelectedFeedback(null)}
          onCopy={handleCopy}
          copiedField={copiedField}
        />
      )}
    </div>
  );
}
