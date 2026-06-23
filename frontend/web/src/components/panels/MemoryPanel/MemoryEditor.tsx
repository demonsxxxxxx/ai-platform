import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Pencil, Save } from "lucide-react";
import toast from "react-hot-toast";
import { EditorSidebar } from "../../common/EditorSidebar";
import { memoryApi, type MemoryItem } from "../../../services/api/memory";
import {
  TYPE_OPTIONS_LIST,
  TYPE_STYLES,
  TYPE_DOTS,
  SOURCE_OPTIONS_LIST,
  SOURCE_STYLES,
  SOURCE_DOTS,
} from "./constants";

export function MemoryEditor({
  memory,
  onClose,
  onSaved,
  relativeTime,
}: {
  memory?: MemoryItem | null;
  onClose: () => void;
  onSaved: () => void;
  relativeTime: (dateStr: string | null) => string;
}) {
  const { t } = useTranslation();
  const isEdit = !!memory;

  const [title, setTitle] = useState(memory?.title ?? "");
  const [content, setContent] = useState(memory?.content ?? "");
  const [summary, setSummary] = useState(memory?.summary ?? "");
  const [memoryType, setMemoryType] = useState(memory?.memory_type ?? "user");
  const [source, setSource] = useState(memory?.source ?? "manual");
  const [tagsInput, setTagsInput] = useState(memory?.tags?.join(", ") ?? "");
  const [saving, setSaving] = useState(false);
  const [loadingContent, setLoadingContent] = useState(
    isEdit && !!memory?.has_full_content,
  );
  const typeStyle = TYPE_STYLES[memoryType] ?? TYPE_STYLES.user;
  const sourceStyle = SOURCE_STYLES[source] ?? SOURCE_STYLES.manual;

  useEffect(() => {
    if (!memory?.has_full_content) return;
    let cancelled = false;
    memoryApi
      .get(memory.memory_id)
      .then((full) => {
        if (!cancelled) {
          setContent(full.content);
          setSummary(full.summary ?? full.content);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoadingContent(false);
      });
    return () => {
      cancelled = true;
    };
  }, [memory]);

  const handleSave = async () => {
    if (!content.trim() || content.trim().length < 5) {
      toast.error(t("memory.contentRequired"));
      return;
    }
    setSaving(true);
    try {
      const tags = tagsInput
        .split(/[,，]/)
        .map((t) => t.trim())
        .filter(Boolean);

      if (isEdit && memory) {
        await memoryApi.update(memory.memory_id, {
          title: title.trim() || undefined,
          content: content.trim(),
          summary: summary.trim() || undefined,
          memory_type: memoryType,
          tags,
          source,
        });
        toast.success(t("memory.updateSuccess"));
      } else {
        await memoryApi.create({
          title: title.trim() || undefined,
          content: content.trim(),
          summary: summary.trim() || undefined,
          memory_type: memoryType,
          tags,
        });
        toast.success(t("memory.createSuccess"));
      }
      onSaved();
      onClose();
    } catch {
      toast.error(t("memory.saveError"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <EditorSidebar
      open={true}
      onClose={onClose}
      title={isEdit ? t("memory.editTitle") : t("memory.createTitle")}
      subtitle={
        isEdit ? (
          <>
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium leading-none ${typeStyle}`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  TYPE_DOTS[memoryType] ?? TYPE_DOTS.user
                }`}
              />
              {t(`memory.type.${memoryType}`)}
            </span>
            <span
              className={`ml-1.5 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${sourceStyle}`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  SOURCE_DOTS[source] ?? SOURCE_DOTS.manual
                }`}
              />
              {t(`memory.source.${source}`, source)}
            </span>
            <span className="ml-2 text-[11px] text-theme-text-secondary">
              {relativeTime(memory?.updated_at ?? null)}
            </span>
          </>
        ) : undefined
      }
      icon={isEdit ? <Pencil size={16} /> : <Plus size={16} />}
      footer={
        <div className="flex items-center gap-3">
          <button onClick={onClose} className="btn-secondary">
            {t("common.cancel")}
          </button>
          <div className="flex-1" />
          <button
            onClick={handleSave}
            disabled={saving || loadingContent}
            className="btn-primary inline-flex items-center gap-1.5"
          >
            <Save size={14} className={saving ? "animate-pulse" : ""} />
            {saving ? t("memory.saving") : t("common.save")}
          </button>
        </div>
      }
    >
      <div className="es-form">
        <div className="es-section">
          <div className="es-field">
            <label className="es-label">{t("memory.titleLabel")}</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t("memory.titlePlaceholder")}
              className="enterprise-field-control es-input"
              maxLength={80}
            />
          </div>

          <div className="es-field">
            <label className="es-label">{t("memory.summaryLabel")}</label>
            <input
              type="text"
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              placeholder={t("memory.summaryPlaceholder")}
              className="enterprise-field-control es-input"
              maxLength={300}
            />
          </div>
        </div>

        <div className="es-section">
          <div className="es-field">
            <label className="es-label">{t("memory.typeLabel")}</label>
            <div className="flex flex-wrap gap-2">
              {TYPE_OPTIONS_LIST.map((opt) => {
                const selected = memoryType === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setMemoryType(opt.value)}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-all ${
                      selected
                        ? `${
                            TYPE_STYLES[opt.value]
                          } border-transparent shadow-sm ring-1 ring-[var(--theme-primary)]`
                        : "border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-card)]"
                    }`}
                  >
                    <span
                      className={`h-2 w-2 rounded-full ${TYPE_DOTS[opt.value]}`}
                    />
                    {t(opt.labelKey)}
                  </button>
                );
              })}
            </div>
          </div>

          {isEdit && (
            <div className="es-field">
              <label className="es-label">{t("memory.sourceLabel")}</label>
              <div className="flex flex-wrap gap-2">
                {SOURCE_OPTIONS_LIST.map((opt) => {
                  const selected = source === opt.value;
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setSource(opt.value)}
                      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-all ${
                        selected
                          ? `${
                              SOURCE_STYLES[opt.value]
                            } border-transparent shadow-sm ring-1 ring-[var(--theme-primary)]`
                          : "border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-card)]"
                      }`}
                    >
                      <span
                        className={`h-2 w-2 rounded-full ${
                          SOURCE_DOTS[opt.value]
                        }`}
                      />
                      {t(opt.labelKey)}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        <div className="es-section">
          <label className="es-label">{t("memory.contentLabel")}</label>
          {loadingContent ? (
            <div className="flex min-h-48 items-center justify-center rounded-lg border border-dashed border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)]">
              <svg
                className="h-5 w-5 animate-spin text-theme-text-secondary"
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
            </div>
          ) : (
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder={t("memory.contentPlaceholder")}
              className="enterprise-field-control es-textarea min-h-48"
              rows={8}
            />
          )}
        </div>

        <div className="es-section">
          <label className="es-label">{t("memory.tagsLabel")}</label>
          <input
            type="text"
            value={tagsInput}
            onChange={(e) => setTagsInput(e.target.value)}
            placeholder={t("memory.tagsPlaceholder")}
            className="enterprise-field-control es-input"
          />
          {tagsInput.trim() && (
            <div className="flex flex-wrap gap-1.5">
              {tagsInput
                .split(/[,，]/)
                .map((tag) => tag.trim())
                .filter(Boolean)
                .slice(0, 8)
                .map((tag) => (
                  <span key={tag} className="es-chip">
                    {tag}
                  </span>
                ))}
            </div>
          )}
        </div>
      </div>
    </EditorSidebar>
  );
}
