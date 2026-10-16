import { Button, TabsRoot, TabsList, TabsTab, TabsIndicator } from "@lobehub/ui/base-ui";
import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import type { FileEntry } from "./SkillForm.types";
import { getFileIcon } from "./SkillForm.utils";

export function FileTabs({
  files,
  activeFileIndex,
  onSelect,
  onRemove,
  untitledLabel,
  panelId,
}: {
  files: FileEntry[];
  activeFileIndex: number;
  onSelect: (i: number) => void;
  onRemove: (i: number) => void;
  untitledLabel: string;
  panelId: string;
}) {
  const { t } = useTranslation();
  const listRef = useRef<HTMLDivElement>(null);
  return (
    <TabsRoot value={activeFileIndex} onValueChange={(value) => onSelect(value as number)}>
      <TabsList ref={listRef} className="overflow-x-auto scrollbar-none" aria-label={t("skills.form.files", "文件")} variant="rounded">
        {files.map((file, index) => {
          const name = file.path ? file.path.split("/").pop() || file.path : untitledLabel;
          return <div key={index} className="flex shrink-0 items-center gap-0.5">
            <TabsTab value={index} id={`${panelId}-tab-${index}`} aria-controls={panelId} size="small" variant="rounded" title={file.path || untitledLabel}>
              {getFileIcon(file.path || "untitled")}
              <span className="max-w-[120px] truncate sm:max-w-[200px]">{name}</span>
            </TabsTab>
            {files.length > 1 ? <Button type="text" size="small" aria-label={`${t("common.close")} ${name}`} tabIndex={activeFileIndex === index ? 0 : -1}
              onClick={(event) => {
                event.stopPropagation(); onRemove(index);
                requestAnimationFrame(() => listRef.current?.querySelector<HTMLElement>("[role=tab][aria-selected=true]")?.focus());
              }} icon={<X size={12} />} /> : null}
          </div>;
        })}
        <TabsIndicator variant="rounded" />
      </TabsList>
    </TabsRoot>
  );
}
