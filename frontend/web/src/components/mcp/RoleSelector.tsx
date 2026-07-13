import { useState, useEffect, useRef } from "react";
import { ChevronDown, X, Search, Shield } from "lucide-react";
import { useTranslation } from "react-i18next";
import { roleApi } from "../../services/api/role";

interface RoleSelectorProps {
  selectedRoles: string[];
  onChange: (roles: string[]) => void;
}

interface RoleInfo {
  name: string;
  description?: string;
  is_system: boolean;
}

export function RoleSelector({ selectedRoles, onChange }: RoleSelectorProps) {
  const { t } = useTranslation();

  const [availableRoles, setAvailableRoles] = useState<RoleInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const dropdownRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

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

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
        setSearch("");
      }
    }
    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen]);

  const filteredRoles = search
    ? availableRoles.filter((r) =>
        r.name.toLowerCase().includes(search.toLowerCase()),
      )
    : availableRoles;

  const toggleRole = (name: string) => {
    if (selectedRoles.includes(name)) {
      onChange(selectedRoles.filter((r) => r !== name));
    } else {
      onChange([...selectedRoles, name]);
    }
  };

  const removeRole = (name: string) => {
    onChange(selectedRoles.filter((r) => r !== name));
  };

  const closeDropdown = (restoreFocus = false) => {
    setIsOpen(false);
    setSearch("");
    if (restoreFocus) triggerRef.current?.focus();
  };

  const handleTriggerKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>,
  ) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setIsOpen(true);
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      closeDropdown();
    }
  };

  const handleDropdownKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeDropdown(true);
    }
  };

  return (
    <div ref={dropdownRef} className="relative">
      <div className="enterprise-form-input flex min-h-[38px] flex-wrap items-center gap-1 px-2 py-1.5">
        {selectedRoles.map((name) => (
            <span
              key={name}
              className="es-chip rounded-md text-xs"
            >
              <Shield size={10} />
              {name}
              <button
                type="button"
                data-mcp-role-chip-remove
                onClick={() => removeRole(name)}
                className="btn-icon ml-0.5 h-5 w-5 p-0"
                aria-label={t("mcp.form.removeRole", { role: name })}
              >
                <X size={12} />
              </button>
            </span>
        ))}
        <button
          ref={triggerRef}
          type="button"
          data-mcp-role-selector-trigger
          onClick={() => setIsOpen((open) => !open)}
          onKeyDown={handleTriggerKeyDown}
          aria-expanded={isOpen}
          aria-controls="mcp-role-options"
          aria-haspopup="dialog"
          aria-label={t("mcp.form.allowedRoles")}
          className="flex min-h-8 min-w-9 flex-1 items-center gap-2 text-left text-xs text-[var(--theme-text-secondary)]"
        >
          {selectedRoles.length === 0 ? (
            <span>{loading ? "..." : t("mcp.form.allRoles")}</span>
          ) : (
            <span className="sr-only">{t("mcp.form.allowedRoles")}</span>
          )}
          <ChevronDown
            size={14}
            className={`ml-auto shrink-0 transition-transform ${
              isOpen ? "rotate-180" : ""
            }`}
          />
        </button>
      </div>

      {/* Dropdown */}
      {isOpen && (
        <div
          id="mcp-role-options"
          role="dialog"
          aria-modal="false"
          aria-label={t("mcp.form.allowedRoles")}
          onKeyDown={handleDropdownKeyDown}
          className="enterprise-select-dropdown absolute z-10 mt-1 w-full"
        >
          {/* Search */}
          <div className="border-b border-[var(--theme-border)] p-2">
            <div className="flex items-center gap-1.5 rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 dark:bg-stone-900">
              <Search
                size={12}
                className="text-[var(--theme-text-secondary)]"
              />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t("mcp.form.searchRoles")}
                className="flex-1 bg-transparent text-xs text-[var(--theme-text)] placeholder:text-[var(--theme-text-secondary)] focus:outline-none"
                autoFocus
              />
            </div>
          </div>

          {/* Options */}
          <fieldset className="max-h-48 overflow-y-auto p-1">
            <legend className="sr-only">{t("mcp.form.allowedRoles")}</legend>
            {loading ? (
              <div className="py-3 text-center text-xs text-[var(--theme-text-secondary)]">
                ...
              </div>
            ) : availableRoles.length === 0 ? (
              <div className="py-3 text-center text-xs text-[var(--theme-text-secondary)]">
                {t("mcp.form.noRoles")}
              </div>
            ) : filteredRoles.length === 0 ? (
              <div className="py-3 text-center text-xs text-[var(--theme-text-secondary)]">
                {t("mcp.form.noMatchingRoles")}
              </div>
            ) : (
              filteredRoles.map((role) => (
                <label
                  key={role.name}
                  className="enterprise-select-option"
                >
                  <input
                    type="checkbox"
                    checked={selectedRoles.includes(role.name)}
                    onChange={() => toggleRole(role.name)}
                    className="rounded border-[var(--theme-border)] accent-teal-700 focus:ring-teal-700/20"
                  />
                  <div className="flex-1 min-w-0">
                    <span className="text-xs font-medium text-[var(--theme-text)]">
                      {role.name}
                    </span>
                    {role.description && (
                      <span className="ml-1.5 truncate text-[10px] text-[var(--theme-text-secondary)]">
                        {role.description}
                      </span>
                    )}
                  </div>
                  {role.is_system && (
                    <span className="es-chip rounded-md px-1 py-0.5 text-[9px]">
                      {t("mcp.card.system")}
                    </span>
                  )}
                </label>
              ))
            )}
          </fieldset>

          {selectedRoles.length > 0 && (
            <div className="border-t border-[var(--theme-border)] p-2">
              <button
                type="button"
                onClick={() => onChange([])}
                className="w-full text-center text-xs text-[var(--theme-text-secondary)] transition-colors hover:text-red-500 dark:hover:text-red-400"
              >
                {t("mcp.form.clearAll")}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
