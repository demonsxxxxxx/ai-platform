import { Button, Checkbox, Input, Tag, PopoverRoot, PopoverTriggerElement, PopoverPortal, PopoverPositioner, PopoverPopup } from "@lobehub/ui/base-ui";
import { useState, useEffect, useId } from "react";
import { ChevronDown, X, Search, Shield } from "lucide-react";
import { useTranslation } from "react-i18next";
import { roleApi } from "../../services/api/role";

interface RoleSelectorProps {
  selectedRoles: string[];
  onChange: (roles: string[]) => void;
  disabled?: boolean;
}

interface RoleInfo {
  name: string;
  description?: string;
  is_system: boolean;
}

export function RoleSelector({ selectedRoles, onChange, disabled = false }: RoleSelectorProps) {
  const { t } = useTranslation();

  const [availableRoles, setAvailableRoles] = useState<RoleInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const popupId = useId();

  useEffect(() => {
    setLoading(true);
    roleApi
      .list({ limit: 200 })
      .then((response) => {
        setAvailableRoles(
          response.roles.map((r) => ({
            name: r.name,
            description: r.description,
            is_system: r.is_system,
          })),
        );
      })
      .catch(() => setAvailableRoles([]))
      .finally(() => setLoading(false));
  }, []);

  const filteredRoles = search ? availableRoles.filter((role) => role.name.toLowerCase().includes(search.toLowerCase())) : availableRoles;
  const toggleRole = (name: string) => {
    if (disabled) return;
    onChange(selectedRoles.includes(name) ? selectedRoles.filter((role) => role !== name) : [...selectedRoles, name]);
  };
  return (
    <PopoverRoot open={isOpen} onOpenChange={(open) => { setIsOpen(open); if (!open) setSearch(""); }}>
      <div className="flex min-h-10 flex-wrap items-center gap-1.5">
        {selectedRoles.map((name) => <Tag key={name} icon={<Shield size={10} />}>
          {name}
          <Button type="text" size="small" disabled={disabled} data-mcp-role-chip-remove aria-label={t("mcp.form.removeRole", { role: name })}
            onClick={() => onChange(selectedRoles.filter((role) => role !== name))}><X size={12} /></Button>
        </Tag>)}
        <PopoverTriggerElement disabled={disabled}>
          <Button type="default" disabled={disabled} data-mcp-role-selector-trigger aria-label={t("mcp.form.allowedRoles")} aria-controls={popupId}
            onKeyDown={(event) => { if (event.key === "ArrowDown") { event.preventDefault(); setIsOpen(true); } }}
          >{selectedRoles.length === 0 ? loading ? "..." : t("mcp.form.allRoles") : t("mcp.form.allowedRoles")}<ChevronDown size={14} /></Button>
        </PopoverTriggerElement>
      </div>
      <PopoverPortal>
        <PopoverPositioner placement="bottomLeft">
          <PopoverPopup id={popupId} aria-label={t("mcp.form.allowedRoles")} style={{ minWidth: 260, maxWidth: "calc(100vw - 32px)" }}>
            <Input autoFocus prefix={<Search size={12} />} value={search} onChange={(event) => setSearch(event.target.value)}
              disabled={disabled} aria-label={t("mcp.form.searchRoles")} placeholder={t("mcp.form.searchRoles")} />
            <fieldset className="my-2 max-h-48 space-y-1 overflow-y-auto" disabled={disabled}>
              <legend className="sr-only">{t("mcp.form.allowedRoles")}</legend>
              {loading ? <p className="py-3 text-center text-xs">...</p> : filteredRoles.length === 0 ? <p className="py-3 text-center text-xs">{t(availableRoles.length === 0 ? "mcp.form.noRoles" : "mcp.form.noMatchingRoles")}</p> : filteredRoles.map((role) => (
                <label key={role.name} className="flex items-center gap-2 py-1.5 text-xs">
                  <Checkbox aria-label={role.name} checked={selectedRoles.includes(role.name)} disabled={disabled} onChange={() => toggleRole(role.name)} />
                  <span className="min-w-0 flex-1">{role.name}{role.description ? <span className="ml-1.5 text-[var(--theme-text-secondary)]">{role.description}</span> : null}</span>
                  {role.is_system ? <Tag size="small">{t("mcp.card.system")}</Tag> : null}
                </label>
              ))}
            </fieldset>
            {selectedRoles.length > 0 ? <Button type="text" danger block disabled={disabled} onClick={() => onChange([])}>{t("mcp.form.clearAll")}</Button> : null}
          </PopoverPopup>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}
