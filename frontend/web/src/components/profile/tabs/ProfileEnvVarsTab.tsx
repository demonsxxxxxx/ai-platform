import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Trash2, Braces, Pencil, X, Check } from "lucide-react";
import { toast } from "react-hot-toast";
import { envvarApi } from "../../../services/api/envvar";
import type { EnvVarResponse } from "../../../services/api/envvar";
import { useAuth } from "../../../hooks/useAuth";
import { Permission } from "../../../types/auth";
import { SkeletonList } from "../../skeletons";
import { LoadingSpinner } from "../../common/LoadingSpinner";
import { ConfirmDialog } from "../../common/ConfirmDialog";

const ENV_KEY_REGEX = /^[A-Za-z_][A-Za-z0-9_]*$/;
const MAX_VALUE_LENGTH = 4096;

export function ProfileEnvVarsTab() {
  const { t } = useTranslation();
  const { hasAnyPermission } = useAuth();

  const canRead = hasAnyPermission([Permission.ENVVAR_READ]);
  const canWrite = hasAnyPermission([Permission.ENVVAR_WRITE]);
  const canDelete = hasAnyPermission([Permission.ENVVAR_DELETE]);

  const [vars, setVars] = useState<EnvVarResponse[]>([]);
  const [loading, setLoading] = useState(true);

  // 新建状态
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [adding, setAdding] = useState(false);

  // 编辑状态（不回填旧值，直接输入新值覆盖）
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editingValue, setEditingValue] = useState("");
  const [saving, setSaving] = useState(false);

  // 删除确认框
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const fetchVars = useCallback(async () => {
    if (!canRead) return;
    setLoading(true);
    try {
      const res = await envvarApi.list();
      setVars(res.variables);
    } catch {
      toast.error(t("envVars.fetchFailed"));
    } finally {
      setLoading(false);
    }
  }, [canRead, t]);

  useEffect(() => {
    fetchVars();
  }, [fetchVars]);

  // 添加新变量
  const handleAdd = async () => {
    const trimmedKey = newKey.trim();
    const trimmedValue = newValue.trim();
    if (!trimmedKey || !trimmedValue) return;
    if (!ENV_KEY_REGEX.test(trimmedKey)) {
      toast.error(t("envVars.invalidKey"));
      return;
    }
    if (trimmedValue.length > MAX_VALUE_LENGTH) {
      toast.error(t("envVars.valueTooLong"));
      return;
    }
    setAdding(true);
    try {
      await envvarApi.set(trimmedKey, trimmedValue);
      toast.success(t("envVars.added"));
      setNewKey("");
      setNewValue("");
      fetchVars();
    } catch (err) {
      toast.error((err as Error).message || t("envVars.addFailed"));
    } finally {
      setAdding(false);
    }
  };

  // 开始编辑（不请求旧值，直接输入新值）
  const startEdit = (key: string) => {
    setEditingKey(key);
    setEditingValue("");
  };

  // 保存编辑
  const saveEdit = async () => {
    if (!editingKey || !editingValue.trim()) return;
    setSaving(true);
    try {
      await envvarApi.set(editingKey, editingValue.trim());
      toast.success(t("envVars.updated"));
      setEditingKey(null);
      setEditingValue("");
      fetchVars();
    } catch {
      toast.error(t("envVars.updateFailed"));
    } finally {
      setSaving(false);
    }
  };

  // 取消编辑
  const cancelEdit = () => {
    setEditingKey(null);
    setEditingValue("");
  };

  // 删除
  const handleDelete = async (key: string) => {
    setDeleteTarget(key);
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      await envvarApi.delete(deleteTarget);
      toast.success(t("envVars.deleted"));
      fetchVars();
    } catch {
      toast.error(t("envVars.deleteFailed"));
    } finally {
      setDeleteTarget(null);
    }
  };

  if (!canRead) {
    return (
      <div className="enterprise-empty-state enterprise-empty-state--compact text-sm">
        {t("common.noPermission")}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <ConfirmDialog
        isOpen={deleteTarget !== null}
        title={t("envVars.confirmDelete", { key: deleteTarget ?? "" })}
        message={t("envVars.description")}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
        variant="danger"
      />
      <div className="enterprise-subtle-panel p-4">
        <div className="mb-3 flex items-center gap-2">
          <Braces size={15} className="text-teal-700 dark:text-teal-300" />
          <h3 className="text-xs font-semibold uppercase text-[var(--theme-text-secondary)]">
            {t("envVars.title")}
          </h3>
        </div>
        <p className="mb-3 text-xs text-[var(--theme-text-secondary)]">
          {t("envVars.description")}
        </p>

        {/* 添加新变量 */}
        {canWrite && (
          <div className="mb-3 flex gap-2">
            <input
              type="text"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder={t("envVars.keyPlaceholder")}
              className="enterprise-form-input min-h-8 flex-1 text-xs"
              onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            />
            <input
              type="password"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              placeholder={t("envVars.valuePlaceholder")}
              className="enterprise-form-input min-h-8 flex-1 text-xs"
              onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            />
            <button
              onClick={handleAdd}
              disabled={adding || !newKey.trim() || !newValue.trim()}
              className="btn-primary h-8 w-8 shrink-0 justify-center p-0"
              aria-label={t("envVars.add")}
            >
              {adding ? (
                <LoadingSpinner size="xs" color="text-white" />
              ) : (
                <Plus size={14} />
              )}
            </button>
          </div>
        )}

        {/* 变量列表 */}
        {loading ? (
          <SkeletonList count={4} className="py-1" />
        ) : vars.length === 0 ? (
          <div className="enterprise-empty-state enterprise-empty-state--compact py-6 text-xs">
            {t("envVars.empty")}
          </div>
        ) : (
          <div className="space-y-1.5">
            {vars.map((envVar) => (
              <div
                key={envVar.key}
                className="group flex items-center gap-2 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3 py-2 dark:bg-stone-900"
              >
                {editingKey === envVar.key ? (
                  <>
                    <span className="shrink-0 font-mono text-xs font-medium text-[var(--theme-text)]">
                      {envVar.key}
                    </span>
                    <span className="text-[var(--theme-text-quaternary)]">
                      =
                    </span>
                    <input
                      type="password"
                      value={editingValue}
                      onChange={(e) => setEditingValue(e.target.value)}
                      placeholder={t("envVars.newValuePlaceholder")}
                      className="enterprise-form-input min-h-7 flex-1 px-2 py-0.5 font-mono text-xs"
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === "Enter") saveEdit();
                        if (e.key === "Escape") cancelEdit();
                      }}
                    />
                    <button
                      onClick={saveEdit}
                      disabled={saving || !editingValue.trim()}
                      className="btn-icon shrink-0 text-green-600 disabled:opacity-40"
                      aria-label={t("common.save")}
                    >
                      {saving ? (
                        <LoadingSpinner size="xs" />
                      ) : (
                        <Check size={12} />
                      )}
                    </button>
                    <button
                      onClick={cancelEdit}
                      className="btn-icon shrink-0"
                      aria-label={t("common.cancel")}
                    >
                      <X size={12} />
                    </button>
                  </>
                ) : (
                  <>
                    <span className="max-w-[40%] shrink-0 truncate font-mono text-xs font-medium text-[var(--theme-text)]">
                      {envVar.key}
                    </span>
                    <span className="text-[var(--theme-text-quaternary)]">
                      =
                    </span>
                    <span className="min-w-0 flex-1 select-none font-mono text-xs text-[var(--theme-text-secondary)]">
                      ••••••••
                    </span>
                    <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                      {canWrite && (
                        <button
                          onClick={() => startEdit(envVar.key)}
                          className="btn-icon"
                          title={t("envVars.edit")}
                        >
                          <Pencil size={12} />
                        </button>
                      )}
                      {canDelete && (
                        <button
                          onClick={() => handleDelete(envVar.key)}
                          className="btn-icon text-red-500 hover:text-red-600 dark:hover:text-red-400"
                          title={t("envVars.delete")}
                        >
                          <Trash2 size={12} />
                        </button>
                      )}
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        )}

        {vars.length > 0 && (
          <div className="mt-2 text-right text-[10px] text-[var(--theme-text-secondary)]">
            {t("envVars.count", { count: vars.length })}
          </div>
        )}
      </div>
    </div>
  );
}
