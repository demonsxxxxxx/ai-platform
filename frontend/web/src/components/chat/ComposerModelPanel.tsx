import {
  type CSSProperties,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
} from "react";
import { Bot } from "lucide-react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import type { ModelOption } from "../../services/api/modelPublic";

export interface ComposerModelPanelProps {
  models: ModelOption[];
  currentModelId?: string;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  onSelectModel: (modelId: string, modelValue: string) => void;
  searchSeed?: string;
}

export function ComposerModelPanel({
  models,
  currentModelId,
  isOpen,
  onOpenChange,
  onSelectModel,
  searchSeed,
}: ComposerModelPanelProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [anchorStyle, setAnchorStyle] = useState<CSSProperties>();

  useEffect(() => {
    if (isOpen) setQuery(searchSeed ?? "");
  }, [isOpen, searchSeed]);

  useLayoutEffect(() => {
    if (!isOpen) return;

    const updatePosition = () => {
      if (!window.matchMedia("(min-width: 640px)").matches) {
        setAnchorStyle(undefined);
        return;
      }
      const anchor =
        document.querySelector<HTMLElement>("[data-composer-model-trigger]") ??
        document.querySelector<HTMLElement>(".chat-input-container");
      if (!anchor) return;
      const rect = anchor.getBoundingClientRect();
      const width = Math.min(320, window.innerWidth - 16);
      setAnchorStyle({
        bottom: window.innerHeight - rect.top + 8,
        left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
        width,
      });
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [isOpen]);

  const filteredModels = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return models;
    return models.filter((model) =>
      [model.label, model.value, model.description ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(normalized),
    );
  }, [models, query]);

  if (!isOpen) return null;

  return createPortal(
    <>
      <div
        data-yields-sidebar
        className="fixed inset-0 z-[300] bg-transparent"
        onClick={() => onOpenChange(false)}
      />
      <section
        data-composer-model-panel
        role="dialog"
        aria-label={t("composerCommand.modelSelector.title", "Select model")}
        className="fixed inset-x-0 bottom-0 z-[301] flex max-h-[min(70dvh,22rem)] flex-col overflow-hidden rounded-t-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] text-[var(--theme-text)] shadow-[0_8px_24px_rgba(18,38,63,0.12)] sm:inset-auto sm:rounded-lg"
        style={anchorStyle}
      >
        <h2 className="sr-only">
          {t("composerCommand.modelSelector.title", "Select model")}
        </h2>
        <div className="overflow-y-auto p-2">
          {filteredModels.map((model) => {
            const active = model.id === currentModelId;
            return (
              <button
                key={model.id}
                type="button"
                data-composer-model-option={model.id}
                aria-current={active ? "true" : undefined}
                className={`mb-1 flex h-11 w-full items-center gap-2.5 rounded-md px-3 text-left text-sm transition-colors ${
                  active
                    ? "bg-[var(--theme-primary-light)] text-[var(--theme-primary)]"
                    : "text-[var(--theme-text)] hover:bg-[var(--theme-bg-sidebar)]"
                }`}
                onClick={() => {
                  onSelectModel(model.id, model.value);
                  onOpenChange(false);
                }}
              >
                <Bot size={18} className="shrink-0" />
                <span className="min-w-0 flex-1 truncate font-medium">
                  {model.label}
                </span>
              </button>
            );
          })}
          {filteredModels.length === 0 && (
            <div className="px-4 py-8 text-center text-sm text-[var(--theme-text-secondary)]">
              {t("composerCommand.modelSelector.empty", "No matching models")}
            </div>
          )}
        </div>
      </section>
    </>,
    document.body,
  );
}
