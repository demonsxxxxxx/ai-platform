import { useState, useEffect, useRef } from "react";
import { ChevronDown, X, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import { envvarApi } from "../../services/api/envvar";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types/auth";

interface EnvKeysSelectorProps {
  selectedKeys: string[];
  onChange: (keys: string[]) => void;
}

export function EnvKeysSelector({
  selectedKeys,
  onChange,
}: EnvKeysSelectorProps) {
  const { t } = useTranslation();
  const { hasAnyPermission } = useAuth();

  const [availableKeys, setAvailableKeys] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!hasAnyPermission([Permission.ENVVAR_READ])) return;
    setLoading(true);
    envvarApi
      .list()
      .then((res) => {
        setAvailableKeys(res.variables.map((v) => v.key).sort());
      })
      .catch(() => {
        setAvailableKeys([]);
      })
      .finally(() => setLoading(false));
  }, [hasAnyPermission]);

  // Close dropdown on outside click
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

  const filteredKeys = search
    ? availableKeys.filter((k) =>
        k.toLowerCase().includes(search.toLowerCase()),
      )
    : availableKeys;

  const toggleKey = (key: string) => {
    if (selectedKeys.includes(key)) {
      onChange(selectedKeys.filter((k) => k !== key));
    } else {
      onChange([...selectedKeys, key]);
    }
  };

  const removeKey = (key: string, e: React.MouseEvent) => {
    e.stopPropagation();
    onChange(selectedKeys.filter((k) => k !== key));
  };

  return (
    <div ref={dropdownRef} className="relative">
      {/* Selected keys as chips */}
      <div
        onClick={() => setIsOpen(!isOpen)}
        className="enterprise-form-input flex min-h-[38px] cursor-pointer flex-wrap items-center gap-1 px-2 py-1.5"
      >
        {selectedKeys.length === 0 ? (
          <span className="text-xs text-[var(--theme-text-secondary)]">
            {loading ? "..." : t("mcp.form.envKeysPlaceholder")}
          </span>
        ) : (
          selectedKeys.map((key) => (
            <span
              key={key}
              className="es-chip rounded-md px-1.5 py-0.5 font-mono text-xs"
            >
              {key}
              <button
                type="button"
                onClick={(e) => removeKey(key, e)}
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
                placeholder={t("mcp.form.envKeysSearch")}
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
            ) : availableKeys.length === 0 ? (
              <div className="py-3 text-center text-xs text-[var(--theme-text-secondary)]">
                {t("mcp.form.noEnvVars")}
              </div>
            ) : filteredKeys.length === 0 ? (
              <div className="py-3 text-center text-xs text-[var(--theme-text-secondary)]">
                {t("mcp.form.noMatchingKeys")}
              </div>
            ) : (
              filteredKeys.map((key) => (
                <label
                  key={key}
                  className="enterprise-select-option"
                >
                  <input
                    type="checkbox"
                    checked={selectedKeys.includes(key)}
                    onChange={() => toggleKey(key)}
                    className="rounded border-[var(--theme-border)] accent-teal-700 focus:ring-teal-700/20"
                  />
                  <code className="font-mono text-xs text-[var(--theme-text)]">
                    {key}
                  </code>
                </label>
              ))
            )}
          </div>

          {selectedKeys.length > 0 && (
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
