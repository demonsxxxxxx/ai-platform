import { memo, useState, useCallback, useEffect, useRef, useMemo } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, Check, Info, Pin, PinOff } from "lucide-react";
import toast from "react-hot-toast";
import { useTranslation } from "react-i18next";
import { ModelIconImg } from "./modelIcon.tsx";
import { shouldCloseModelSelector } from "./modelSelectorGuards";
import type { ModelOption } from "../../services/api/modelPublic";

const MAX_PINNED = 10;

interface ModelItemProps {
  model: ModelOption;
  isSelected: boolean;
  isPinned: boolean;
  onSelect: () => void;
  onTogglePin: () => void;
  canPin: boolean;
}

const ModelItem = memo(function ModelItem({
  model,
  isSelected,
  isPinned,
  onSelect,
  onTogglePin,
  canPin,
}: ModelItemProps) {
  const { t } = useTranslation();
  const [showTip, setShowTip] = useState(false);
  const tipTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const iconRef = useRef<HTMLSpanElement>(null);

  const show = useCallback(() => {
    clearTimeout(tipTimer.current);
    setShowTip(true);
  }, []);

  const hide = useCallback(() => {
    tipTimer.current = setTimeout(() => setShowTip(false), 150);
  }, []);

  const toggle = useCallback((e: React.TouchEvent) => {
    e.stopPropagation();
    setShowTip((v) => !v);
  }, []);

  // Close tooltip when clicking outside
  useEffect(() => {
    if (!showTip) return;
    const handleClick = (e: MouseEvent) => {
      if (iconRef.current && !iconRef.current.contains(e.target as Node)) {
        setShowTip(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [showTip]);

  const tipPlacement = useRef<"top" | "bottom">("top");

  const tipStyle = (() => {
    if (!showTip || !iconRef.current) return undefined;
    const rect = iconRef.current.getBoundingClientRect();
    const estimatedHeight = Math.min(model.description!.length * 0.6, 120) + 24;
    const showAbove = rect.top > estimatedHeight + 8;
    tipPlacement.current = showAbove ? "top" : "bottom";
    if (showAbove) {
      return {
        left: rect.left + rect.width / 2,
        top: rect.top - 8,
        transform: "translate(-50%, -100%)",
      };
    }
    return {
      left: rect.left + rect.width / 2,
      top: rect.bottom + 8,
      transform: "translate(-50%, 0)",
    };
  })();

  return (
    <div className="group/model-item relative">
      <div className="flex items-center gap-2.5 px-3 py-2.5 hover:bg-stone-100/80 dark:hover:bg-stone-700/50 transition-colors">
        <button
          type="button"
          onClick={onSelect}
          className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
        >
          <ModelIconImg
            model={model.value}
            provider={model.provider}
            size={22}
          />
          <div className="flex items-center gap-1 flex-1 min-w-0">
            <span className="text-sm text-stone-700 dark:text-stone-200 truncate">
              {model.label}
            </span>
            {model.description && (
              <span
                ref={iconRef}
                className="inline-flex items-center shrink-0 cursor-pointer ml-0.5"
                onMouseEnter={show}
                onMouseLeave={hide}
                onTouchStart={toggle}
              >
                <Info
                  size={14}
                  className="text-stone-400 hover:text-stone-600 dark:text-stone-500 dark:hover:text-stone-300 transition-colors"
                />
                {showTip && (
                  <span
                    className="fixed z-[60] max-w-[240px] w-max rounded-lg bg-stone-700 dark:bg-stone-900 px-2.5 py-1.5 text-xs leading-relaxed text-white shadow-lg whitespace-normal"
                    style={tipStyle}
                    onTouchStart={(e) => e.stopPropagation()}
                  >
                    {model.description}
                    {tipPlacement.current === "top" ? (
                      <span className="absolute left-1/2 -translate-x-1/2 top-full border-[5px] border-transparent border-t-stone-700 dark:border-t-stone-900" />
                    ) : (
                      <span className="absolute left-1/2 -translate-x-1/2 bottom-full border-[5px] border-transparent border-b-stone-700 dark:border-b-stone-900" />
                    )}
                  </span>
                )}
              </span>
            )}
          </div>
          {isSelected && (
            <Check
              size={16}
              className="text-stone-500 dark:text-stone-400 shrink-0"
            />
          )}
        </button>
        <button
          type="button"
          onClick={() => {
            if (canPin || isPinned) onTogglePin();
          }}
          className={`shrink-0 p-0.5 rounded transition-opacity ${
            isPinned
              ? "opacity-100"
              : "opacity-0 group-hover/model-item:opacity-100"
          } ${!canPin && !isPinned ? "cursor-not-allowed" : "cursor-pointer"}`}
          title={
            isPinned
              ? t("profile.unpinModel")
              : canPin
                ? t("profile.pinModel")
                : t("profile.maxPinnedModels", { max: MAX_PINNED })
          }
        >
          {isPinned ? (
            <Pin
              size={14}
              className="text-stone-500 dark:text-stone-400"
              fill="currentColor"
            />
          ) : (
            <PinOff
              size={14}
              className={
                canPin
                  ? "text-stone-400 hover:text-stone-600 dark:text-stone-500 dark:hover:text-stone-300"
                  : "text-stone-300 dark:text-stone-600"
              }
            />
          )}
        </button>
      </div>
    </div>
  );
});

interface ModelSelectorProps {
  models: ModelOption[];
  currentModelId: string;
  pinnedModelIds?: string[];
  onTogglePinnedModel?: (modelId: string) => void;
  onSelectModel: (modelId: string, modelValue: string) => void;
}

const ModelSelector = memo(function ModelSelector({
  models,
  currentModelId,
  pinnedModelIds = [],
  onTogglePinnedModel,
  onSelectModel,
}: ModelSelectorProps) {
  const { t } = useTranslation();
  const [showSelector, setShowSelector] = useState(false);
  const [defaultTick, setDefaultTick] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const currentModelInfo = models.find((m) => m.id === currentModelId);

  const isDefault = (() => {
    void defaultTick;
    return localStorage.getItem("defaultModelId") === currentModelId;
  })();

  const handleSetDefault = useCallback(() => {
    if (!currentModelInfo) return;
    localStorage.setItem("defaultModelId", currentModelId);
    localStorage.setItem("defaultModel", currentModelInfo.value);
    window.dispatchEvent(
      new CustomEvent("model-preference-updated", {
        detail: { modelId: currentModelId, modelValue: currentModelInfo.value },
      }),
    );
    setDefaultTick((t) => t + 1);
    toast.success(t("profile.defaultModelSet"));
  }, [currentModelId, currentModelInfo, t]);

  useEffect(() => {
    const handler = () => setDefaultTick((t) => t + 1);
    window.addEventListener("model-preference-updated", handler);
    return () =>
      window.removeEventListener("model-preference-updated", handler);
  }, []);

  const handleSelectModel = useCallback(
    (modelId: string, modelValue: string) => {
      onSelectModel(modelId, modelValue);
      setShowSelector(false);
    },
    [onSelectModel],
  );

  const toggleSelector = useCallback(() => {
    setShowSelector((prev) => !prev);
  }, []);

  useEffect(() => {
    if (!showSelector) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (
        shouldCloseModelSelector(
          event.target as Node | null,
          containerRef.current,
          dropdownRef.current,
        )
      ) {
        setShowSelector(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [showSelector]);

  const dropdownStyle = (() => {
    if (!showSelector || !containerRef.current) return undefined;
    const rect = containerRef.current.getBoundingClientRect();
    return {
      top: rect.bottom + 8,
      left: rect.left,
    };
  })();

  // Sort models: pinned first (in pinned order), then unpinned (original order)
  const sortedModels = useMemo(() => {
    const pinnedSet = new Set(pinnedModelIds);
    const pinned = models.filter((m) => pinnedSet.has(m.id));
    const pinnedOrdered = pinned.sort(
      (a, b) => pinnedModelIds.indexOf(a.id) - pinnedModelIds.indexOf(b.id),
    );
    const unpinned = models.filter((m) => !pinnedSet.has(m.id));
    return { pinned: pinnedOrdered, unpinned };
  }, [models, pinnedModelIds]);

  const hasPinned = sortedModels.pinned.length > 0;
  const hasUnpinned = sortedModels.unpinned.length > 0;

  if (models.length === 0) return null;

  return (
    <div
      ref={containerRef}
      className="relative"
      onClick={(e) => e.stopPropagation()}
    >
      <button
        onClick={toggleSelector}
        className="flex items-center gap-1.5 hover:opacity-70 transition-opacity"
      >
        <span className="text-base font-semibold text-stone-600 dark:text-stone-300 max-w-[200px] truncate">
          {currentModelInfo?.label || currentModelId}
        </span>
        <ChevronDown
          size={16}
          className={`text-stone-400 dark:text-stone-300 transition-transform duration-200 ${
            showSelector ? "rotate-180" : ""
          }`}
        />
      </button>

      {!isDefault && (
        <button
          onClick={handleSetDefault}
          className="absolute left-[1px] top-full mt-[1px] text-[0.7rem] text-stone-400 hover:text-stone-600 dark:text-stone-500 dark:hover:text-stone-300 transition-colors cursor-pointer select-none"
        >
          {t("profile.setDefault")}
        </button>
      )}

      {showSelector &&
        createPortal(
          <div
            ref={dropdownRef}
            className="fixed z-[301] w-72 rounded-xl bg-white dark:bg-stone-800 shadow-lg border border-stone-200 dark:border-stone-700 overflow-hidden animate-scale-in"
            style={dropdownStyle}
          >
            <div className="max-h-96 overflow-y-auto overscroll-contain">
              {sortedModels.pinned.map((model) => (
                <ModelItem
                  key={model.id}
                  model={model}
                  isSelected={model.id === currentModelId}
                  isPinned={true}
                  onSelect={() => handleSelectModel(model.id, model.value)}
                  onTogglePin={() => onTogglePinnedModel?.(model.id)}
                  canPin={true}
                />
              ))}
              {hasPinned && hasUnpinned && (
                <div
                  className="mx-3 border-t"
                  style={{ borderColor: "var(--theme-border)" }}
                />
              )}
              {sortedModels.unpinned.map((model) => (
                <ModelItem
                  key={model.id}
                  model={model}
                  isSelected={model.id === currentModelId}
                  isPinned={false}
                  onSelect={() => handleSelectModel(model.id, model.value)}
                  onTogglePin={() => onTogglePinnedModel?.(model.id)}
                  canPin={pinnedModelIds.length < MAX_PINNED}
                />
              ))}
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
});

export { ModelSelector };
