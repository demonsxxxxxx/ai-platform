import React, { useState, useEffect, useRef } from "react";
import { Settings, ChevronDown, Check } from "lucide-react";
import { useTranslation } from "react-i18next";

interface Role {
  id: string;
  name: string;
}

interface RoleSelectorProps {
  roles: Role[];
  selectedRoleId: string | null;
  onSelectRole: (roleId: string) => void;
}

export const RoleSelector = React.memo(function RoleSelector({
  roles,
  selectedRoleId,
  onSelectRole,
}: RoleSelectorProps) {
  const { t } = useTranslation();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    if (!dropdownOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [dropdownOpen]);

  const selectedRole = roles.find((r) => r.id === selectedRoleId);

  return (
    <div ref={dropdownRef}>
      <div className="relative">
        <button
          onClick={() => setDropdownOpen(!dropdownOpen)}
          aria-expanded={dropdownOpen}
          aria-haspopup="listbox"
          className="flex w-full items-center justify-between rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-4 py-3 text-sm font-medium text-[var(--theme-text)]"
        >
          <span className="flex items-center gap-2">
            <Settings size={16} className="text-stone-500" />
            {selectedRole?.name || t("agentConfig.selectRole")}
          </span>
          <ChevronDown
            size={18}
            className={`text-stone-500 transition-transform ${
              dropdownOpen ? "rotate-180" : ""
            }`}
          />
        </button>

        {dropdownOpen && (
          <div
            className="absolute z-10 mt-1 w-full overflow-hidden rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] shadow-[0_12px_28px_rgba(15,23,42,0.12)]"
            role="listbox"
          >
            {roles.map((role) => (
              <button
                key={role.id}
                role="option"
                aria-selected={selectedRoleId === role.id}
                onClick={() => {
                  onSelectRole(role.id);
                  setDropdownOpen(false);
                }}
                className={`flex w-full items-center justify-between px-4 py-3 text-sm transition-colors first:rounded-t-lg last:rounded-b-lg ${
                  selectedRoleId === role.id
                    ? "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text)]"
                    : "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]"
                }`}
              >
                <span>{role.name}</span>
                {selectedRoleId === role.id && (
                  <Check
                    size={16}
                    className="text-stone-600 dark:text-stone-400"
                  />
                )}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
});
