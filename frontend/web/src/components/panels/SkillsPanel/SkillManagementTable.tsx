import { Button, Checkbox, DropdownMenu, Table, Tag } from "@lobehub/ui/base-ui";
import {
  Download,
  Archive,
  Boxes,
  Code2,
  FileText,
  FileType2,
  Globe2,
  Package2,
  Presentation,
  Pencil,
  Power,
  MoreHorizontal,
  Search,
  Sheet,
  Store,
  UploadCloud,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  SkillCatalogEntry,
  SkillCatalogStatus,
} from "./skillCatalogEntries";

const INTERACTIVE_ROW_TARGET =
  'button, a, input, select, textarea, [role="button"], [role="link"], [role="checkbox"], [contenteditable="true"]';

function isInteractiveRowTarget(
  target: EventTarget | null,
  currentTarget: EventTarget | null,
): boolean {
  return (
    target !== currentTarget &&
    target instanceof Element &&
    target.closest(INTERACTIVE_ROW_TARGET) !== null
  );
}

interface SkillManagementTableProps {
  canBatch: boolean;
  canDelete: boolean;
  canEdit: boolean;
  canExport: boolean;
  canPublish: boolean;
  canToggle: boolean;
  onDelete: (name: string) => void;
  onEdit: (skill: NonNullable<SkillCatalogEntry["runtimeSkill"]>) => void;
  onExportZip: (name: string) => void;
  onSelectDetail: (skillId: string) => void;
  onSelectAll: () => void;
  onSelectSkill: (name: string) => void;
  onToggle: (name: string) => void;
  onUploadVersion: (skillName: string) => void;
  selectedNames: Set<string>;
  selectedSkillId: string | null;
  entries: SkillCatalogEntry[];
}

function catalogStatusKey(status: SkillCatalogStatus): string {
  if (status === "available") return "skills.managementTable.distributed";
  if (status === "hidden") return "skills.managementTable.hidden";
  if (status === "disabled") return "skills.managementTable.distributionDisabled";
  if (status === "internal") return "skills.managementTable.internal";
  return "skills.managementTable.notPublished";
}

function updatedDateLabel(value?: string): string {
  if (!value) return "-";
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp)
    ? new Date(timestamp).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : "-";
}

function skillIcon(name: string) {
  const normalized = name.toLocaleLowerCase();
  if (normalized.includes("spreadsheet") || normalized.includes("excel")) return Sheet;
  if (normalized === "pdf" || normalized.includes("pdf")) return FileType2;
  if (normalized.includes("presentation") || normalized.includes("slide")) return Presentation;
  if (normalized.includes("research")) return Search;
  if (normalized.includes("web") || normalized.includes("browser")) return Globe2;
  if (normalized.includes("code") || normalized.includes("interpreter")) return Code2;
  if (normalized.includes("document") || normalized.includes("word")) return FileText;
  return Package2;
}

export function SkillManagementTable({
  canBatch,
  canDelete,
  canEdit,
  canExport,
  canPublish,
  canToggle,
  onDelete,
  onEdit,
  onExportZip,
  onSelectDetail,
  onSelectAll,
  onSelectSkill,
  onToggle,
  onUploadVersion,
  selectedNames,
  selectedSkillId,
  entries,
}: SkillManagementTableProps) {
  const { t } = useTranslation();
  const [openActionName, setOpenActionName] = useState<string | null>(null);
  const selectableEntries = entries.filter((entry) => entry.actionName !== null);
  const allSelectableSelected =
    selectableEntries.length > 0 &&
    selectableEntries.every((entry) => selectedNames.has(entry.actionName!));

  return (
    <div aria-label={t("skills.managementTable.listLabel")} data-skill-management-table><Table
 size="small" pagination={false} className="skill-management-table" scroll={{ x: 720 }}
 dataSource={entries}
 rowKey={(entry) => entry.id}
onRow={(entry) => {  return ({"aria-selected": entry.id === selectedSkillId,
"className": `skill-management-table__row ${entry.id === selectedSkillId ? "skill-management-table__row--selected" : ""}`,
"data-catalog-status": entry.catalogStatus,
"data-skill-catalog-item": entry.id,
"onClick": () => onSelectDetail(entry.id),
"onKeyDown": (event) => {
              if (isInteractiveRowTarget(event.target, event.currentTarget)) {
                return;
              }
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelectDetail(entry.id);
              }
            },
"role": "row",
"tabIndex": 0}); }}
 columns={[
{ hidden: !canBatch, width: 44, align: "center", key: "selection", title: (<Checkbox
              aria-label={
                allSelectableSelected
                  ? t("common.deselectAll")
                  : t("common.selectAll")
              }
              checked={allSelectableSelected}
              onChange={onSelectAll}
            />),
render: (_value, entry) =>  {
const actionName = entry.actionName;
return (<>
{actionName ? (<><Checkbox
                  aria-label={t("skills.managementTable.selectSkill", { name: entry.displayName })}
                  checked={selectedNames.has(actionName)}
                  onClick={(event) => event.stopPropagation()}
                  onChange={() => onSelectSkill(actionName)}
                /></>) : null}
</>);
} },
{ key: "identity", width: 240, title: t("skills.managementTable.skill"),
onCell: () => ({"data-label": t("skills.managementTable.skill")}),
render: (_value, entry) =>  {
const SkillIcon = skillIcon(entry.displayName);
return (<div className="skill-management-table__identity">
<span className="skill-management-table__icon" aria-hidden="true">
                <SkillIcon size={18} />
              </span>
              <div className="min-w-0">
                <p
                  className="truncate text-sm font-semibold text-[var(--theme-text)]"
                  title={entry.displayName}
                >
                  {entry.displayName}
                </p>
                <p
                  className="mt-0.5 line-clamp-2 text-xs leading-5 text-[var(--theme-text-secondary)]"
                  title={entry.description || undefined}
                >
                  {entry.description || t("skills.noDescription")}
                </p>
                {entry.tags.length > 0 ? (
                  <div
                    aria-label={t("skills.managementTable.tags")}
                    className="mt-1.5 flex max-w-full gap-1 overflow-hidden"
                  >
                    {entry.tags.slice(0, 2).map((tag) => (
                      <Tag
                        className="max-w-28 truncate"
                        size="small"
                        key={tag}
                        title={tag}
                      >
                        {tag}
                      </Tag>
                    ))}
                    {entry.tags.length > 2 ? (
                      <Tag size="small">
                        +{entry.tags.length - 2}
                      </Tag>
                    ) : null}
                  </div>
                ) : null}
              </div>
</div>);
} },
{ key: "runtime", width: 120, title: t("skills.managementTable.runtimeStatus"),
onCell: () => ({"className": "skill-management-table__runtime",
"data-label": t("skills.managementTable.runtimeStatus")}),
render: (_value, entry) => (<>
<Tag size="small" color={entry.runtimeEnabled ? "success" : entry.catalogStatus === "internal" ? "warning" : undefined}>
                {entry.catalogStatus === "internal"
                  ? t("skills.managementTable.internalRuntime")
                  : entry.runtimeEnabled === null
                    ? t("skills.managementTable.notPublished")
                    : entry.runtimeEnabled
                      ? t("skills.managementTable.enabled")
                      : t("skills.managementTable.disabled")}
              </Tag>
</>) },
{ key: "catalog", width: 150, title: t("skills.managementTable.catalogStatus"),
onCell: () => ({"className": "skill-management-table__distribution min-w-0",
"data-label": t("skills.managementTable.catalogStatus")}),
render: (_value, entry) =>  {
const CatalogStatusIcon =
            entry.catalogStatus === "internal" ? Boxes : Store;
return (<>
<Tag size="small" icon={<CatalogStatusIcon aria-hidden="true" size={13} />} color={entry.catalogStatus === "available" ? "info" : ["internal", "hidden"].includes(entry.catalogStatus) ? "warning" : undefined}>
                {t(catalogStatusKey(entry.catalogStatus))}
              </Tag>
              {entry.publishedCatalogName ? (
                <span
                  className="mt-1 block truncate text-[11px] text-[var(--theme-text-secondary)]"
                  title={entry.publishedCatalogName}
                >
                  {entry.publishedCatalogName}
                </span>
              ) : null}
</>);
} },
{ key: "version", width: 110, responsive: ["xl"], title: t("skills.managementTable.package"),
onCell: () => ({"className": "skill-management-table__package",
"data-label": t("skills.managementTable.package")}),
render: (_value, entry) => (<>
<span
                className="block truncate font-mono text-xs text-[var(--theme-text)]"
                title={entry.version ?? undefined}
              >
                {entry.version || "-"}
              </span>
</>) },
{ key: "updated", width: 130, responsive: ["xl"], title: t("skills.managementTable.updatedAt"),
onCell: () => ({"className": "skill-management-table__updated text-xs text-[var(--theme-text-secondary)]",
"data-label": t("skills.managementTable.updatedAt")}),
render: (_value, entry) => (<>
{updatedDateLabel(entry.updatedAt ?? undefined)}
</>) },
{ key: "actions", width: 90, align: "right", title: t("skills.managementTable.actions"),
onCell: () => ({"data-label": t("skills.managementTable.actions")}),
render: (_value, entry) =>  {
const actionName = entry.actionName;
const canAct = actionName !== null && entry.runtimeSkill !== null;
const rowCanToggle = canToggle && canAct;
const rowCanEdit = canEdit && canAct;
const rowCanExport = canExport && canAct;
const rowCanPublish = canPublish && canAct;
const rowCanDelete = canDelete && canAct;
const hasActions =
            rowCanToggle || rowCanEdit || rowCanExport || rowCanPublish || rowCanDelete;
return (<div className="skill-management-table__actions">
{rowCanPublish ? (
                <Button type="text"
                  aria-label={t("skills.managementTable.updateVersionSkill", {
                    name: entry.displayName,
                  })}
                  size="small"
                  onClick={(event) => {
                    event.stopPropagation();
                    onUploadVersion(actionName);
                  }}
                  title={t("skills.managementTable.updateVersion")}
                  htmlType="button"
                >
                  <UploadCloud aria-hidden="true" size={16} />
                </Button>
              ) : null}
              {hasActions ? (
                <DropdownMenu
                  open={openActionName === actionName}
                  onOpenChange={(open) => setOpenActionName(open ? actionName : null)}
                  placement="bottomRight"
                  popupProps={{
                    "aria-label": `${entry.displayName} ${t("common.menu")}`,
                    onClick: (event) => event.stopPropagation(),
                    onKeyDown: (event) => event.stopPropagation(),
                  }}
                  items={[
                    rowCanToggle ? { key: "toggle", icon: Power, label: t(entry.runtimeEnabled ? "skills.managementTable.disable" : "skills.managementTable.enable"), onClick: () => { setOpenActionName(null); onToggle(actionName); } } : null,
                    rowCanEdit ? { key: "edit", icon: Pencil, label: t("skills.managementTable.edit"), onClick: () => { setOpenActionName(null); onEdit(entry.runtimeSkill!); } } : null,
                    rowCanExport ? { key: "export", icon: Download, label: t("skills.exportZip"), onClick: () => { setOpenActionName(null); onExportZip(actionName); } } : null,
                    rowCanDelete ? { key: "archive", icon: Archive, label: t("skills.managementTable.delete"), onClick: () => { setOpenActionName(null); onDelete(actionName); } } : null,
                  ]}
                >
                  <Button type="text" size="small" aria-label={t("common.menu")} htmlType="button" onClick={(event) => event.stopPropagation()}>
                    <MoreHorizontal aria-hidden="true" size={17} />
                  </Button>
                </DropdownMenu>
              ) : null}
              {!hasActions ? (
                <span className="text-xs text-[var(--theme-text-secondary)]">
                  {t(
                    entry.catalogStatus === "internal"
                      ? "skills.managementTable.internalDependency"
                      : "skills.managementTable.readOnly",
                  )}
                </span>
              ) : null}
</div>);
} }
]}
/></div>
  );
}
