import { useEffect, useRef } from "react";
import {
  Bot,
  Brain,
  Check,
  FileText,
  Layers,
  Lock,
  Search,
  Sparkles,
  Wrench,
} from "lucide-react";
import type { SlashCommandGroup, SlashCommandOption } from "./slashCommand";

interface SlashCommandMenuProps {
  options: SlashCommandOption[];
  highlightedIndex: number;
  placement?: {
    left: number;
    width: number;
    bottom: number;
    maxHeight: number;
  } | null;
  onHover: (index: number) => void;
  onSelect: (option: SlashCommandOption) => void;
  onClose: () => void;
}

const GROUP_META: Record<
  SlashCommandGroup,
  {
    label: string;
    Icon: React.ElementType;
  }
> = {
  skill: { label: "Skills", Icon: Sparkles },
  mcp: { label: "MCP", Icon: Wrench },
  agent: { label: "agents", Icon: Bot },
  model: { label: "models", Icon: Brain },
  file: { label: "files", Icon: FileText },
  context: { label: "context", Icon: Layers },
};

export function SlashCommandMenu({
  options,
  highlightedIndex,
  placement,
  onHover,
  onSelect,
  onClose,
}: SlashCommandMenuProps) {
  const anchorRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    const el = itemRefs.current[highlightedIndex];
    if (el) {
      el.scrollIntoView({ block: "nearest" });
    }
  }, [highlightedIndex]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (anchorRef.current?.contains(event.target as Node)) return;
      onClose();
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [onClose]);

  return (
    <div
      ref={anchorRef}
      className="slash-command-anchor"
      style={
        placement
          ? ({
              "--slash-command-left": `${placement.left}px`,
              "--slash-command-width": `${placement.width}px`,
              "--slash-command-bottom": `${placement.bottom}px`,
              "--slash-command-max-height": `${placement.maxHeight}px`,
            } as React.CSSProperties)
          : undefined
      }
    >
      <div className="slash-command-menu" role="listbox" aria-label="Slash commands">
        <div className="slash-command-header">
          <Search size={14} />
          <span>Choose Skills, MCP, agents, models, files, or context</span>
        </div>
        <div className="slash-command-list">
          {options.map((option, index) => {
            const { Icon, label: groupLabel } = GROUP_META[option.group];
            const isActive = index === highlightedIndex;
            return (
              <button
                key={option.id}
                ref={(el) => {
                  itemRefs.current[index] = el;
                }}
                type="button"
                role="option"
                aria-selected={isActive}
                aria-disabled={option.disabled || undefined}
                disabled={option.disabled}
                className="slash-command-item"
                data-active={isActive ? "" : undefined}
                data-disabled={option.disabled ? "" : undefined}
                onClick={() => onSelect(option)}
                onMouseEnter={() => onHover(index)}
              >
                <span className="slash-command-icon" aria-hidden="true">
                  <Icon size={16} />
                </span>
                <span className="slash-command-text">
                  <span className="slash-command-title">
                    {option.kind === "command" ? option.command : option.label}
                    {option.selected && (
                      <Check size={13} className="slash-command-check" />
                    )}
                  </span>
                  <span className="slash-command-description">
                    {option.unavailableReason ||
                      option.description ||
                      groupLabel}
                  </span>
                </span>
                <span className="slash-command-badge">
                  {option.disabled ? <Lock size={12} /> : groupLabel}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
