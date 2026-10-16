import { Button, Drawer, PopoverRoot, PopoverPortal, PopoverPositioner, PopoverPopup } from "@lobehub/ui/base-ui";
import { useEffect, useMemo, useState } from "react";
import { Bot, X } from "lucide-react";
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
  const [isMobile, setIsMobile] = useState(() => typeof window !== "undefined" && window.innerWidth < 640);
  useEffect(() => { if (isOpen) setQuery(searchSeed ?? ""); }, [isOpen, searchSeed]);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 639px)");
    const change = () => setIsMobile(media.matches);
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  const filteredModels = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return models;
    return models.filter((model) => [model.label, model.value, model.description ?? ""].join(" ").toLowerCase().includes(normalized));
  }, [models, query]);
  const title = t("composerCommand.modelSelector.title", "选择模型");
  const content = (
    <div data-composer-model-panel className="flex max-h-[min(70dvh,22rem)] flex-col overflow-y-auto">
      {filteredModels.map((model) => (
        <Button key={model.id} type={model.id === currentModelId ? "primary" : "text"} block
          data-composer-model-option={model.id} aria-current={model.id === currentModelId ? "true" : undefined}
          className="mb-1 h-11 min-w-0 justify-start text-left"
          onClick={() => { onSelectModel(model.id, model.value); onOpenChange(false); }}
        ><Bot size={18} /><span className="min-w-0 truncate">{model.label}</span></Button>
      ))}
      {filteredModels.length === 0 ? <p className="px-4 py-8 text-center text-sm">{t("composerCommand.modelSelector.empty", "暂无匹配模型")}</p> : null}
    </div>
  );
  if (isMobile) return (
    <Drawer open={isOpen} onClose={() => onOpenChange(false)} placement="bottom" height="auto" title={title} closable={false}
      extra={<Button type="text" aria-label={t("common.close")} onClick={() => onOpenChange(false)}><X size={18} /></Button>}
    >{content}</Drawer>
  );
  return (
    <PopoverRoot open={isOpen} onOpenChange={onOpenChange}>
      <PopoverPortal>
        <PopoverPositioner placement="topLeft" anchor={() => document.querySelector<HTMLElement>("[data-composer-model-trigger]") ?? document.querySelector<HTMLElement>(".chat-input-container")}>
          <PopoverPopup aria-label={title} style={{ width: 320, maxWidth: "calc(100vw - 16px)" }}>{content}</PopoverPopup>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}
