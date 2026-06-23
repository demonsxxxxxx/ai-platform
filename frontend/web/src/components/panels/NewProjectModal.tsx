import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

interface NewProjectModalProps {
  icon: string;
  name: string;
  onIconChange: (icon: string) => void;
  onNameChange: (name: string) => void;
  onCreate: () => void;
  onClose: () => void;
}

export function NewProjectModal({
  icon,
  name,
  onIconChange,
  onNameChange,
  onCreate,
  onClose,
}: NewProjectModalProps) {
  const { t } = useTranslation();

  return createPortal(
    <div
      data-yields-sidebar
      className="fixed inset-0 z-[300] flex items-center justify-center"
    >
      <div
        className="absolute inset-0 bg-[var(--theme-overlay-strong)]"
        onClick={onClose}
      />
      <div className="enterprise-modal-shell relative w-[90vw] max-w-md p-5 space-y-3">
        <h3 className="text-sm font-semibold text-stone-800 dark:text-stone-100">
          {t("sidebar.newProject")}
        </h3>
        <p className="text-xs text-stone-400 dark:text-stone-500">
          {t("sidebar.projectHint")}
        </p>

        <div className="flex items-center gap-2 enterprise-subtle-panel focus-within:ring-2 focus-within:ring-stone-400/30 transition-all">
          <input
            type="text"
            value={icon}
            onChange={(e) => onIconChange(e.target.value)}
            placeholder={t("sidebar.projectName")}
            className="w-8 text-sm bg-transparent text-stone-500 dark:text-stone-400 placeholder-stone-400 focus:outline-none"
          />
          <div className="w-px h-5 bg-stone-300 dark:bg-stone-600" />
          <input
            ref={(el) => {
              if (el) el.focus();
            }}
            type="text"
            value={name}
            onChange={(e) => onNameChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                onCreate();
                onClose();
              }
              if (e.key === "Escape") {
                onClose();
                onNameChange("");
              }
            }}
            placeholder={t("sidebar.projectName")}
            className="flex-1 text-sm bg-transparent text-stone-700 dark:text-stone-200 placeholder-stone-400 focus:outline-none"
          />
        </div>
        <div className="flex justify-end gap-2 pt-1">
          <button
            onClick={() => {
              onClose();
              onNameChange("");
              onIconChange("📁");
            }}
            className="btn-secondary"
          >
            {t("common.cancel")}
          </button>
          <button
            onClick={() => {
              onCreate();
              onClose();
            }}
            disabled={!name.trim()}
            className="btn-primary disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {t("common.create")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
