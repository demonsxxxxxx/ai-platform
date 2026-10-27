// Adapted from assistant-ui Elements composer.tsx model menu at 727a02dc (MIT).
import { Button, Drawer, Input, PopoverRoot, PopoverPortal, PopoverPositioner, PopoverPopup } from "@lobehub/ui/base-ui";
import { useEffect, useMemo, useState } from "react";
import { Check, Search, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ModelOption } from "../../../../services/api/modelPublic";
import { ComposerMenuItem } from "./composer";

export function ComposerModelPicker({ models, currentModelId, isOpen, onOpenChange, onSelectModel, searchSeed, selectionMode = "explicit" }: {
  models: ModelOption[];
  currentModelId?: string;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  onSelectModel: (id: string, value: string, mode?: "auto" | "explicit") => void;
  selectionMode?: "auto" | "explicit";
  searchSeed?: string;
}) {
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
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return normalized ? models.filter((model) => [model.label, model.value, model.description ?? ""].join(" ").toLowerCase().includes(normalized)) : models;
  }, [models, query]);
  const title = t("composerCommand.modelSelector.title");
  const content = <div data-slot="composer-model-picker" data-composer-model-panel className="flex max-h-[min(70dvh,22rem)] flex-col gap-2">
    <Input value={query} onChange={(event) => setQuery(event.target.value)} prefix={<Search size={16} />}
      aria-label={t("composerCommand.modelSelector.searchPlaceholder")} placeholder={t("composerCommand.modelSelector.searchPlaceholder")} />
    <div role="listbox" aria-label={title} className="min-h-0 overflow-y-auto">
      {filtered.map((model) => <ComposerMenuItem key={model.id} role="option" aria-selected={model.id === currentModelId && selectionMode === "explicit"}
        data-composer-model-option={model.id} active={model.id === currentModelId && selectionMode === "explicit"}
        onClick={() => { onSelectModel(model.id, model.value, "explicit"); onOpenChange(false); }}>
        <span className="min-w-0 flex-1 truncate">{model.label}</span>
        {model.id === currentModelId && <Check size={16} aria-hidden />}
      </ComposerMenuItem>)}
      {filtered.length === 0 && <p className="px-4 py-8 text-center text-sm">{t("composerCommand.modelSelector.empty")}</p>}
    </div>
  </div>;
  if (isMobile) return <Drawer open={isOpen} onClose={() => onOpenChange(false)} placement="bottom" height="auto" title={title} closable={false}
    extra={<Button type="text" aria-label={t("common.close")} onClick={() => onOpenChange(false)} icon={<X size={18} />} />}>
    {content}
  </Drawer>;
  return <PopoverRoot open={isOpen} onOpenChange={onOpenChange}>
    <PopoverPortal><PopoverPositioner placement="topLeft" anchor={() => document.querySelector<HTMLElement>("[data-composer-model-trigger]")}>
      <PopoverPopup aria-label={title} style={{ width: 320, maxWidth: "calc(100vw - 16px)" }}>{content}</PopoverPopup>
    </PopoverPositioner></PopoverPortal>
  </PopoverRoot>;
}
