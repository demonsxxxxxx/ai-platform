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

  const removeRole = (name: string, e: React.MouseEvent) => {
    e.stopPropagation();
    onChange(selectedRoles.filter((r) => r !== name));
  };

  return (
    <div ref={dropdownRef} className="relative">
      {/* Selected roles as chips */}
      <div
        onClick={() => setIsOpen(!isOpen)}
        className="enterprise-form-input flex min-h-[38px] cursor-pointer flex-wrap items-center gap-1 px-2 py-1.5"
      >
        {selectedRoles.length === 0 ? (
          <span className="text-xs text-[var(--theme-text-secondary)]">
            {loading ? "..." : t("mcp.form.allRoles")}
          </span>
        ) : (
          selectedRoles.map((name) => (
            <span
              key={name}
              className="es-chip rounded-md text-xs"
            >
              <Shield size={10} />
              {name}
              <button
                type="button"
                onClick={(e) => removeRole(name, e)}
                className="btn-icon ml-0.5 h-5 w-5 p-0"
                aria-label={t("mcp.form.clearAll")}
              >
                <X size={12} />
              </button>
            </span>
          ))
        )}
        <ChevronDown
          size={14}
          className={`ml-auto text-[var(--theme-text-secondary)] transition-transform ${
            isOpen ? "rotate-180" : ""
          }`}
        />
      </div>

      {/* Dropdown */}
      {isOpen && (
        <div className="enterprise-select-dropdown absolute z-10 mt-1 w-full">
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
          <div className="max-h-48 overflow-y-auto p-1">
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
          </div>

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
