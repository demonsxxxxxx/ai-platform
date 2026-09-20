import {
  useState,
  useRef,
  useEffect,
  memo,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import {
  Wrench,
  Sparkles,
  Plus,
  ChevronDown,
  Layers,
} from "lucide-react";


export type FeaturePanel =
  | "tools"
  | "skills"
  | "model"
  | "file"
  | "thinking"
  | null;

interface FeatureMenuProps {
  activePanel: FeaturePanel;
  onOpen: (panel: FeaturePanel) => void;
  triggerLabel?: string;
  enabledToolsCount: number;
  totalToolsCount: number;
  enabledSkillsCount: number;
  totalSkillsCount: number;
}

function MenuGroup({
  label,
  icon,
  defaultExpanded = false,
  children,
}: {
  label: string;
  icon: ReactNode;
  defaultExpanded?: boolean;
  children: ReactNode;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  return (
    <div className="feature-menu-group" role="group">
      <button
        type="button"
        className="feature-menu-group-header"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="feature-menu-group-icon">{icon}</span>
        <span className="flex-1 text-left truncate">{label}</span>
        <ChevronDown
          size={16}
          className="feature-menu-chevron"
          data-open={expanded ? "true" : undefined}
        />
      </button>
      <div
        className="feature-menu-group-body"
        data-expanded={expanded ? "" : undefined}
      >
        <div className="feature-menu-group-inner">{children}</div>
      </div>
    </div>
  );
}

function MenuItem({
  icon,
  label,
  badge,
  active,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  badge?: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="feature-menu-item"
      data-active={active ? "" : undefined}
    >
      <span className="feature-menu-item-icon">{icon}</span>
      <span className="flex-1 text-left truncate">{label}</span>
      {badge && <span className="feature-menu-item-badge">{badge}</span>}
    </button>
  );
}

export const FeatureMenu = memo(function FeatureMenu({
  activePanel,
  onOpen,
  triggerLabel,
  enabledToolsCount,
  totalToolsCount,
  enabledSkillsCount,
  totalSkillsCount,
}: FeatureMenuProps) {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (triggerRef.current?.contains(e.target as Node)) return;
      if (dropdownRef.current?.contains(e.target as Node)) return;
      setIsOpen(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen]);

  useEffect(() => {
    if (activePanel) setIsOpen(false);
  }, [activePanel]);

  const getDropdownStyle = (): CSSProperties => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return { display: "none" };
    const vw = window.innerWidth;
    const dropdownW = Math.min(vw < 640 ? 220 : 320, vw - 16);
    const left = Math.max(8, Math.min(rect.left, vw - dropdownW - 8));
    return {
      position: "fixed",
      bottom: window.innerHeight - rect.top + 8,
      left,
      width: dropdownW,
      zIndex: 9999,
    };
  };

  const hasFeatureItems = totalToolsCount > 0 || totalSkillsCount > 0;
  if (!hasFeatureItems) return null;

  const resolvedTriggerLabel = triggerLabel ?? t("chat.features", "功能");

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setIsOpen((prev) => !prev);
        }}
        style={isOpen ? { position: "relative", zIndex: 10000 } : undefined}
        className="chat-tool-btn"
        aria-label={resolvedTriggerLabel}
        title={resolvedTriggerLabel}
      >
        <Plus size={18} />
      </button>

      {isOpen &&
        createPortal(
          <div
            ref={dropdownRef}
            className="feature-menu-dropdown"
            style={{
              ...getDropdownStyle(),
              background: "var(--theme-workbench-panel)",
              borderColor: "var(--theme-border)",
            }}
          >
            {(totalToolsCount > 0 || totalSkillsCount > 0) && (
              <MenuGroup
                label={t("featureMenu.enhance", "增强")}
                icon={<Layers size={18} />}
              >
                {totalToolsCount > 0 && (
                  <MenuItem
                    icon={<Wrench size={18} />}
                    label={t("featureMenu.mcpTools")}
                    badge={`${enabledToolsCount}/${totalToolsCount}`}
                    active={activePanel === "tools"}
                    onClick={() => onOpen("tools")}
                  />
                )}
                {totalSkillsCount > 0 && (
                  <MenuItem
                    icon={<Sparkles size={18} />}
                    label={t("featureMenu.skillsMarketplace")}
                    badge={`${enabledSkillsCount}/${totalSkillsCount}`}
                    active={activePanel === "skills"}
                    onClick={() => onOpen("skills")}
                  />
                )}
              </MenuGroup>
            )}
          </div>,
          document.body,
        )}
    </>
  );
});
