import { Check, Boxes, Search, X } from "lucide-react";
import { useMemo, useState, useEffect } from "react";
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

  useEffect(() => {
    if (!isOpen || searchSeed === undefined) return;
    setQuery(searchSeed);
  }, [isOpen, searchSeed]);

  const filteredModels = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return models;
    return models.filter((model) =>
      [
        model.label,
        model.value,
        model.provider ?? "",
        model.description ?? "",
      ]
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
        className="fixed inset-0 z-[300] bg-[var(--theme-overlay)] animate-fade-in"
        onClick={() => onOpenChange(false)}
      />
      <div
        className="fixed z-[301] sm:inset-0 sm:flex sm:items-center sm:justify-center sm:p-4 inset-x-0 bottom-0 animate-slide-up sm:animate-scale-in"
        onClick={() => onOpenChange(false)}
      >
        <section
          data-composer-model-panel
          className="flex max-h-[85dvh] min-h-[40vh] w-full flex-col overflow-hidden rounded-t-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] text-[var(--theme-text)] shadow-[0_8px_24px_rgba(18,38,63,0.12)] sm:w-[40%] sm:min-w-[600px] sm:rounded-lg"
          onClick={(event) => event.stopPropagation()}
        >
          <header className="relative flex items-center justify-between border-b border-[var(--theme-border)] px-4 py-3 sm:px-5">
            <div className="absolute left-1/2 top-2 h-1 w-10 -translate-x-1/2 rounded-full bg-[var(--theme-border)] sm:hidden" />
            <div className="mt-2 flex items-center gap-3 sm:mt-0">
              <div className="flex size-9 items-center justify-center rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)]">
                <Boxes size={17} className="text-[var(--theme-text-secondary)]" />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-[var(--theme-text)]">
                  {t("composerCommand.modelSelector.title", "Select model")}
                </h2>
                <p className="text-xs text-[var(--theme-text-secondary)]">
                  {t("composerCommand.modelSelector.description", {
                    count: models.length,
                    defaultValue:
                      "Choose an ai-platform approved model for this session.",
                  })}
                </p>
              </div>
            </div>
            <button
              type="button"
              className="rounded-md p-2 text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]"
              onClick={() => onOpenChange(false)}
              aria-label={t("common.close", "Close")}
            >
              <X size={18} />
            </button>
          </header>

          <div className="border-b border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-4 py-3 sm:px-5">
            <label className="relative block">
              <Search
                size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--theme-text-secondary)]"
              />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t(
                  "composerCommand.modelSelector.searchPlaceholder",
                  "Search models",
                )}
                className="h-10 w-full rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] pl-9 pr-3 text-sm text-[var(--theme-text)] outline-none transition-colors placeholder:text-[var(--theme-text-secondary)] focus:border-[var(--theme-ring)] focus:ring-2 focus:ring-[var(--theme-primary-light)]"
              />
            </label>
          </div>

          <div className="flex-1 space-y-1.5 overflow-y-auto p-3">
            {filteredModels.map((model) => {
              const active = model.id === currentModelId;
              return (
                <button
                  key={model.id}
                  type="button"
                  className={`flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left transition-colors ${
                    active
                      ? "bg-[var(--theme-primary)] text-[var(--theme-primary-foreground)]"
                      : "text-[var(--theme-text)] hover:bg-[var(--theme-bg-sidebar)]"
                  }`}
                  onClick={() => {
                    onSelectModel(model.id, model.value);
                    onOpenChange(false);
                  }}
                >
                  <div
                    className={`flex size-9 shrink-0 items-center justify-center rounded-lg border ${
                      active
                        ? "border-white/20 bg-white/10"
                        : "border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)]"
                    }`}
                  >
                    <Boxes size={16} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-semibold">
                        {model.label}
                      </span>
                      {model.provider && (
                        <span
                          className={`rounded-md px-1.5 py-0.5 text-[10px] ${
                            active
                              ? "bg-white/15 text-[var(--theme-primary-foreground-muted)]"
                              : "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]"
                          }`}
                        >
                          {model.provider}
                        </span>
                      )}
                    </div>
                    <p
                      className={`mt-0.5 truncate text-xs ${
                        active
                          ? "text-[var(--theme-primary-foreground-subtle)]"
                          : "text-[var(--theme-text-secondary)]"
                      }`}
                    >
                      {model.description ?? model.value}
                    </p>
                  </div>
                  {active && <Check size={17} className="shrink-0" />}
                </button>
              );
            })}
            {filteredModels.length === 0 && (
              <div className="rounded-lg border border-dashed border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-4 py-6 text-center text-sm text-[var(--theme-text-secondary)]">
                {t("composerCommand.modelSelector.empty", "No matching models")}
              </div>
            )}
          </div>
        </section>
      </div>
    </>,
    document.body,
  );
}
